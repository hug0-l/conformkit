"""AAF and re-conform tests."""
import re
from xml.etree import ElementTree as ET

import pytest

from conformkit.aaf import (AAF_NS, AAFError, place_clips, read_aafx,
                            write_aafx)
from conformkit.conform import (
    ConformError, OpKind, apply_ops, check_reconformable, plan_conform,
)
from conformkit.model import EditList, Event, Transition, parse_track_text
from conformkit.timecode import Timecode, add, rate_for, to_frames

R25 = rate_for("25")
BASE = Timecode(0, 0, 0, 0)


def ev(roll="R1", clip="C1", start=0, dur=100, track="V", src=1000):
    return Event(
        rec_in=add(BASE, start, R25), rec_out=add(BASE, start + dur, R25),
        src_in=add(BASE, src, R25), src_out=add(BASE, src + dur, R25),
        roll_name=roll, clip_name=clip,
        tracks=parse_track_text(track), transition=Transition("C", 0),
    )


def el(events):
    return EditList(events=events, rate=R25, title="L")


# --------------------------------------------------------------- placement

def test_no_overlap_on_a_track():
    events = [ev(start=0, dur=100), ev(start=50, dur=100, roll="R2")]
    placements = place_clips(el(events))
    # overlapping clips must land on different slots
    slots = {(p.track_kind, p.track_index) for p in placements}
    assert len(slots) >= 2


def test_non_overlapping_share_a_track():
    events = [ev(start=0, dur=100), ev(start=100, dur=100, roll="R2")]
    placements = place_clips(el(events))
    assert all(p.track_index == 0 for p in placements)


def test_audio_and_video_get_separate_tracks():
    events = [ev(track="V"), ev(track="A", roll="R2")]
    placements = place_clips(el(events))
    kinds = {(p.track_kind, p.track_index) for p in placements}
    assert ("V", 0) in kinds
    assert ("A", 0) in kinds


def test_placement_is_deterministic():
    events = [ev(start=i * 50, dur=100, roll=f"R{i}") for i in range(10)]
    first = [(p.event.roll_name, p.track_kind, p.track_index)
             for p in place_clips(el(events))]
    for _ in range(5):
        assert [(p.event.roll_name, p.track_kind, p.track_index)
                for p in place_clips(el(events))] == first


def test_minimal_track_count():
    # 3 sequential clips must fit on 1 track
    events = [ev(start=i * 100, dur=100, roll=f"R{i}") for i in range(3)]
    placements = place_clips(el(events))
    assert max(p.track_index for p in placements) == 0


def test_zero_duration_events_are_skipped():
    """Transition anchors (zero-length edits) carry no media and are not
    placed in an AAF, but they must not abort the export."""
    e = ev(start=0, dur=100)
    e.rec_out = e.rec_in
    placements = place_clips(el([e]))
    assert placements == []


# --------------------------------------------------------------- AAFX

def q(tag):
    return f"{{{AAF_NS}}}{tag}"


def text(node, tag):
    return (node.findtext(q(tag)) or "").strip()


def source_clips(root):
    return root.findall(f".//{q('SourceClip')}")


def sequences(root):
    return root.findall(f".//{q('Sequence')}")


def composition_slots(root):
    return root.findall(
        f".//{q('CompositionMob')}/{q('MobSlots')}/{q('TimelineMobSlot')}")


UMID_RE = re.compile(
    r"^urn:x-umid:[0-9a-fA-F]{24}-13-[0-9a-fA-F]{6}-[0-9a-fA-F]{32}$")


def test_aafx_namespace_and_version():
    root = ET.fromstring(write_aafx(el([ev()]), "TEST"))
    assert AAF_NS == "http://www.aafassociation.org/aafx/v1.1/20050628"
    assert root.tag == q("AAF")
    assert root.get("version") == "1.1"
    assert text(root.find(q("Header")), "Version") == "1.1"


def test_aafx_header_definitions_cover_document_references():
    root = ET.fromstring(write_aafx(el([ev(), ev(track="A2", roll="R2")])))
    definitions = root.findall(f".//{q('DataDefinitions')}/{q('DataDefinition')}")
    assert definitions
    uids = set()
    for definition in definitions:
        uid = definition.get(q("uid"))
        assert uid, "DataDefinition is missing aaf:uid"
        assert text(definition, "DefinitionObjectIdentification")
        assert text(definition, "DefinitionObjectName")
        uids.add(uid)

    required = {"DataDef_Picture", "DataDef_Sound", "DataDef_LegacyPicture",
                "DataDef_LegacySound", "DataDef_Timecode"}
    assert required <= uids

    referenced = {(node.text or "").strip()
                  for node in root.findall(f".//{q('ComponentDataDefinition')}")}
    assert referenced
    assert referenced <= uids, f"undefined data definitions: {referenced - uids}"


def test_aafx_has_composition_and_source_mobs():
    events = [ev(roll="R1", clip="R1", start=0, track="V"),
              ev(roll="R2", clip="R2", start=0, track="A1"),
              ev(roll="R3", clip="R3", start=0, track="A2")]
    root = ET.fromstring(write_aafx(el(events)))
    comps = root.findall(f".//{q('CompositionMob')}")
    assert len(comps) == 1
    assert text(comps[0], "MobName") == "L"
    assert composition_slots(root)
    assert text(comps[0], "MobID").startswith("urn:x-umid:")

    source_mobs = root.findall(f".//{q('SourceMob')}")
    # one per (source name, media kind): R1 picture, R2 sound, R3 sound
    assert len(source_mobs) == 3
    descriptors = {}
    for mob in source_mobs:
        assert UMID_RE.match(text(mob, "MobID"))
        essence = mob.find(q("EssenceDescription"))
        assert essence is not None and len(essence) == 1
        descriptors[text(mob, "MobName")] = essence[0].tag.split("}")[-1]
    assert descriptors == {"R1": "CDCIDescriptor",
                           "R2": "PCMDescriptor",
                           "R3": "PCMDescriptor"}


def test_aafx_source_ids_resolve_to_source_mobs():
    events = [ev(roll="R1", clip="R1", track="V", start=0),
              ev(roll="R1", clip="R1", track="V", start=100),
              ev(roll="R2", clip="R2", track="A", start=0)]
    root = ET.fromstring(write_aafx(el(events)))
    source_mobs = {text(m, "MobID"): m
                   for m in root.findall(f".//{q('SourceMob')}")}
    clips = source_clips(root)
    assert len(clips) == 3
    for clip in clips:
        source_id = text(clip, "SourceID")
        assert UMID_RE.match(source_id)
        assert source_id in source_mobs
        slot_ids = {text(s, "SlotID")
                    for s in source_mobs[source_id].findall(
                        f"./{q('MobSlots')}/{q('TimelineMobSlot')}")}
        assert text(clip, "SourceMobSlotID") in slot_ids


def test_aafx_component_lengths_sum():
    events = [ev(start=0, dur=100, track="V"),
              ev(start=150, dur=50, track="V", roll="R2"),
              ev(start=0, dur=100, track="A1", roll="R3")]
    root = ET.fromstring(write_aafx(el(events)))

    for sequence in sequences(root):
        declared = int(text(sequence, "ComponentLength"))
        components = sequence.find(q("Components"))
        assert components is not None
        total = sum(int(text(c, "ComponentLength")) for c in components)
        assert declared == total
        assert total > 0

    # the video slot has a 50-frame gap, filled explicitly
    video = next(s for s in composition_slots(root)
                 if text(s, "SlotName") == "V")
    kinds = [c.tag.split("}")[-1] for c in
             video.find(f".//{q('Components')}")]
    assert kinds == ["SourceClip", "Filler", "SourceClip"]


def test_aafx_source_slot_covers_every_clip_extent():
    events = [ev(start=0, dur=100, track="V", src=1000),
              ev(start=100, dur=100, track="V", src=1200, roll="R2")]
    root = ET.fromstring(write_aafx(el(events)))
    source_mobs = {text(m, "MobID"): m
                   for m in root.findall(f".//{q('SourceMob')}")}
    for clip in source_clips(root):
        mob = source_mobs[text(clip, "SourceID")]
        sequence = mob.find(f".//{q('Sequence')}")
        extent = int(text(sequence, "ComponentLength"))
        end = int(text(clip, "StartTime")) + int(text(clip, "ComponentLength"))
        assert end <= extent


def test_placements_keep_edl_track_labels():
    """A2 must stay A2 even when A1 is free or processed later."""
    events = [ev(track="A2", roll="R2"), ev(track="A1", roll="R1")]
    placements = place_clips(el(events))
    by_roll = {p.event.roll_name: p for p in placements}
    assert by_roll["R2"].track_label == "A2"
    assert by_roll["R1"].track_label == "A1"
    assert by_roll["R1"].track_index != by_roll["R2"].track_index


def test_aafx_preserves_a1_and_a2_placements():
    events = [ev(track="A1", roll="R1", start=0, dur=100),
              ev(track="A2", roll="R2", start=0, dur=100),
              ev(track="A1", roll="R3", start=100, dur=100)]
    root = ET.fromstring(write_aafx(el(events)))
    slots = {text(s, "SlotName"): s for s in composition_slots(root)}
    assert {"A1", "A2"} <= set(slots)

    def clips_in(slot):
        return slot.find(f".//{q('Components')}").findall(q("SourceClip"))

    assert len(clips_in(slots["A1"])) == 2
    assert len(clips_in(slots["A2"])) == 1
    assert text(slots["A1"], "PhysicalTrackNumber") != \
        text(slots["A2"], "PhysicalTrackNumber")


def test_aafx_roundtrip():
    events = [ev(clip="C1", start=0, dur=100, track="V"),
              ev(clip="C2", start=100, dur=100, track="A1", roll="R2"),
              ev(clip="C3", start=50, dur=50, track="A2", roll="R3")]
    xml = write_aafx(el(events), "TEST")
    back = read_aafx(xml, R25)
    assert len(back) == 3
    starts = sorted(to_frames(e.rec_in, R25) for e in back)
    assert starts == [0, 50, 100]
    labels = {str(t) for e in back for t in e.tracks}
    assert {"V", "A1", "A2"} <= labels
    assert {e.roll_name for e in back} == {"C1", "C2", "C3"}


def test_aafx_roundtrip_preserves_track_names():
    events = [ev(track="A1", roll="R1"), ev(track="A2", roll="R2")]
    back = read_aafx(write_aafx(el(events)), R25)
    labels = {str(t) for e in back for t in e.tracks}
    assert labels == {"A1", "A2"}

    named = read_aafx(
        write_aafx(el(events), audio_track_names=["Boom", "Lav"]), R25)
    assert {str(t) for e in named for t in e.tracks} == {"Boom", "Lav"}


def test_aafx_is_deterministic():
    events = [ev(), ev(track="A2", roll="R2")]
    first = write_aafx(el(events), "TEST")
    second = write_aafx(el(events), "TEST")
    assert first == second

    # placement and MobIDs also do not depend on the input list's order
    reordered = write_aafx(el(list(reversed(events))), "TEST")
    assert first == reordered


def test_aafx_has_no_overlapping_clips_per_slot():
    events = [ev(start=0, dur=100), ev(start=50, dur=100, roll="R2")]
    root = ET.fromstring(write_aafx(el(events)))
    for slot in composition_slots(root):
        ranges = []
        cursor = 0
        for component in slot.find(f".//{q('Components')}"):
            length = int(text(component, "ComponentLength"))
            if component.tag == q("SourceClip"):
                ranges.append((cursor, cursor + length))
            cursor += length
        ranges.sort()
        for (s1, e1), (s2, e2) in zip(ranges, ranges[1:]):
            assert e1 <= s2, f"overlap {s1}-{e1} vs {s2}-{e2}"


def test_aafx_does_not_invent_operation_definitions():
    """Transitions are a documented omission: the baseline dictionary has no
    operation definition to reference, so none is fabricated."""
    e = ev()
    e.transition = Transition("D", 25)
    xml = write_aafx(el([e]))
    root = ET.fromstring(xml)
    assert len(source_clips(root)) == 1
    assert "OperationGroup" not in xml
    assert "Transition" not in xml


def test_aafx_custom_track_names():
    events = [ev(track="A1", roll="R1"), ev(track="A1", roll="R2", start=200)]
    xml = write_aafx(el(events), audio_track_names=["Boom", "Lav"])
    root = ET.fromstring(xml)
    names = [text(s, "SlotName") for s in composition_slots(root)]
    assert names == ["Boom"]


# --------------------------------------------------------------- conform

def test_identical_cuts_are_all_keep():
    a = el([ev(start=0), ev(start=200, roll="R2")])
    b = el([ev(start=0), ev(start=200, roll="R2")])
    plan = plan_conform(a, b)
    assert plan.counts() == {"keep": 2}


def test_move_detected():
    a = el([ev(start=0)])
    b = el([ev(start=25)])
    plan = plan_conform(a, b)
    assert plan.counts() == {"move": 1}
    assert plan.ops[0].delta == 25


def test_delete_detected():
    a = el([ev(start=0), ev(start=200, roll="R2")])
    b = el([ev(start=0)])
    plan = plan_conform(a, b)
    assert plan.counts() == {"keep": 1, "delete": 1}


def test_trim_detected():
    a = el([ev(start=0, dur=100)])
    b = el([ev(start=0, dur=50)])
    plan = plan_conform(a, b)
    assert "trim" in plan.counts()


def test_reconformable_rejects_rate_mismatch():
    a = el([ev()])
    b = EditList(events=[ev()], rate=rate_for("24"))
    problems = check_reconformable(a, b)
    assert any("frame rate" in p for p in problems)


def test_reconformable_rejects_unknown_roll():
    a = el([ev(roll="R1")])
    b = el([ev(roll="R9")])
    problems = check_reconformable(a, b)
    assert any("R9" in p for p in problems)


def test_reconformable_rejects_before_session_start():
    a = el([ev(start=1000)])
    b = el([ev(start=0)])
    problems = check_reconformable(a, b, session_start=add(BASE, 500, R25))
    assert any("before the session start" in p for p in problems)


def test_plan_raises_on_unreconformable():
    a = el([ev(roll="R1")])
    b = el([ev(roll="R9")])
    with pytest.raises(ConformError):
        plan_conform(a, b)


def test_apply_reproduces_new_cut():
    a = el([ev(start=0, roll="R1"), ev(start=200, roll="R2"),
            ev(start=400, roll="R3")])
    # new cut: R1 moves, R2 kept, R3 deleted
    b = el([ev(start=50, roll="R1"), ev(start=200, roll="R2")])
    plan = plan_conform(a, b)
    result = apply_ops(a, plan)
    assert len(result) == 2
    assert sorted(to_frames(e.rec_in, R25) for e in result) == [50, 200]


def test_new_material_requires_opt_in():
    """A conform cannot invent source material; unknown rolls are rejected
    unless the caller explicitly allows insertion."""
    a = el([ev(start=0, roll="R1")])
    b = el([ev(start=0, roll="R1"), ev(start=200, roll="R4", src=5000)])
    assert any("R4" in p for p in check_reconformable(a, b))

    # with the flag, the new material becomes an insert op
    problems = check_reconformable(a, b, allow_new_material=True)
    assert problems == []
    plan = plan_conform(a, b, allow_new_material=True)
    assert "insert" in plan.counts()


def test_insert_op_is_recorded():
    a = el([ev(start=0, roll="R1")])
    b = el([ev(start=0, roll="R1"), ev(start=200, roll="R4", src=5000)])
    plan = plan_conform(a, b, allow_new_material=True)
    inserts = [o for o in plan.ops if o.kind == OpKind.INSERT]
    assert len(inserts) == 1
    assert inserts[0].new.roll_name == "R4"
