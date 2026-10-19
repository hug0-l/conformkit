"""AAF and re-conform tests."""
import pytest

from conformkit.aaf import AAFError, place_clips, read_aafx, write_aafx
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

def test_aafx_roundtrip():
    events = [ev(start=0, dur=100), ev(start=100, dur=100, roll="R2")]
    src = el(events)
    xml = write_aafx(src, "TEST")
    assert "amwa.tv/aafx/v2.0" in xml
    back = read_aafx(xml, R25)
    assert len(back) == 2
    starts = sorted(to_frames(e.rec_in, R25) for e in back)
    assert starts == [0, 100]


def test_aafx_has_no_overlapping_clips_per_slot():
    from xml.etree import ElementTree as ET
    events = [ev(start=0, dur=100), ev(start=50, dur=100, roll="R2")]
    xml = write_aafx(el(events))
    root = ET.fromstring(xml)
    ns = {"a": "http://www.amwa.tv/aafx/v2.0/20200101"}
    for slot in root.findall(".//a:TimelineMobSlot", ns):
        ranges = []
        for clip in slot.findall("a:SourceClip", ns):
            # attributes are unqualified even when the element is namespaced
            start = int(clip.get("Start"))
            length = int(clip.get("Length"))
            ranges.append((start, start + length))
        ranges.sort()
        for (s1, e1), (s2, e2) in zip(ranges, ranges[1:]):
            assert e1 <= s2, f"overlap {s1}-{e1} vs {s2}-{e2}"


def test_aafx_emits_transition():
    e = ev()
    e.transition = Transition("D", 25)
    xml = write_aafx(el([e]))
    assert "OperationGroup" in xml
    assert "dissolve" in xml


def test_aafx_track_names():
    events = [ev(track="A", roll="R1"), ev(track="A", roll="R2", start=200)]
    xml = write_aafx(el(events), audio_track_names=["Boom", "Lav"])
    assert 'TrackName="Boom"' in xml


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
