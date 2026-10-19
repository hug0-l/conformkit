"""AAF export as AAFX (AAF 2.0 XML interchange).

Writing binary AAF requires the AAF SDK. AAFX is the standardised XML form of
the same object model and is sufficient for interchange with Pro Tools, Nuendo
and Resolve. Namespace:

    http://www.amwa.tv/aafx/v2.0/20200101

Track placement rule (documented here, derived from required behaviour):
  - clips are placed on the lowest-numbered track of their kind that has no
    overlap with the clip's record range
  - a new track is created only when every existing track of that kind overlaps
  - empty tracks are never emitted
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .model import EditList, Event
from .timecode import Rate, format_tc, to_frames

__all__ = ["AAFError", "place_clips", "write_aafx", "AAF_NS",
           "identify_aaf", "read_aaf_names", "AafContainer"]

AAF_NS = "http://www.amwa.tv/aafx/v2.0/20200101"
AAF_NS_A = "http://www.amwa.tv/aafx/v2.0/20200101"
XML_NS = "http://www.w3.org/XML/1998/namespace"


class AAFError(ValueError):
    pass


# --------------------------------------------------------------- container

def identify_aaf(data: bytes) -> dict:
    """Identify a binary AAF file.

    A binary AAF is an OLE2 compound file whose objects are keyed by SMPTE
    Universal Labels. Both facts are checkable without the AAF SDK.
    """
    from .cfb import CFB_MAGIC, SMPTE_UL_PREFIX, Cfb, CfbError

    if data[:8] != CFB_MAGIC:
        return {"is_aaf": False, "reason": "not an OLE2 compound file"}

    try:
        c = Cfb(data)
    except CfbError as exc:
        return {"is_aaf": False, "reason": str(exc)}

    names = {e.name for e in c.directory()}
    has_aaf_markers = bool(
        names & {"MetaDictionary-1", "Header-"} or
        any(n.startswith(("Properties-", "ClassDefinitions-")) for n in names)
    )
    smpte_hits = data.count(SMPTE_UL_PREFIX)

    return {
        "is_aaf": True,
        "sdk": "AAF",
        "aaf_version": f"{c.major}.{c.minor}",
        "streams": len(c.streams()),
        "smpte_ul_count": smpte_hits,
        "looks_like_aaf": has_aaf_markers,
        "sector_size": c.sector_size,
    }


def read_aaf_names(path_or_bytes) -> list[str]:
    """Recover the human-readable names stored in a binary AAF.

    These are the mob, tape and track names, stored as UTF-16. They are the
    fields a conform tool needs, and they are readable without the AAF SDK.
    """
    import re

    if isinstance(path_or_bytes, (bytes, bytearray)):
        data = bytes(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as fh:
            data = fh.read()

    seen: set[str] = set()
    out: list[str] = []
    for m in re.finditer(rb"(?:[\x20-\x7e]\x00){3,120}", data):
        text = m.group(0).decode("utf-16-le").strip()
        if len(text) < 3 or text in seen:
            continue
        if not any(c.isalpha() for c in text):
            continue
        # skip UTF-16 renderings of container bookkeeping
        if text in ("Root Entry",) or text == "properties":
            continue
        seen.add(text)
        out.append(text)
    return out


class AafContainer:
    """Read-only view of a binary AAF's compound-file container."""

    def __init__(self, path_or_bytes):
        from .cfb import Cfb

        if isinstance(path_or_bytes, (bytes, bytearray)):
            self.data = bytes(path_or_bytes)
            self.path = ""
        else:
            with open(path_or_bytes, "rb") as fh:
                self.data = fh.read()
            self.path = str(path_or_bytes)
        self.cfb = Cfb(self.data)

    def streams(self):
        return self.cfb.streams()

    def directory_all(self):
        """Every directory entry, including storages and the root."""
        return self.cfb.directory()

    def read_stream(self, entry) -> bytes:
        return self.cfb.read(entry)

    def names(self) -> list[str]:
        return read_aaf_names(self.data)

    def info(self) -> dict:
        return identify_aaf(self.data)


@dataclass
class Placement:
    event: Event
    track_index: int
    track_kind: str


def place_clips(el: EditList, trim_ends: bool = False) -> list[Placement]:
    """Assign each event to a track slot with no overlap.

    Zero-length events are skipped: they are transition anchors carrying no
    media. Deterministic: events are processed in record order, and each goes
    to the lowest track index whose last end is <= this clip's record start.
    """
    if el.rate is None:
        raise AAFError("list has no frame rate")
    rate = el.rate

    # per kind, a list of the last occupied end frame for each track index
    occupied: dict[str, list[int]] = {"V": [], "A": []}
    out: list[Placement] = []

    for ev in sorted(el.events, key=lambda e: to_frames(e.rec_in, rate)):
        start = to_frames(ev.rec_in, rate)
        end = to_frames(ev.rec_out, rate)
        if end < start:
            raise AAFError(
                f"event at {format_tc(ev.rec_in, rate)} has negative duration")
        if end == start:
            continue        # transition anchor, no media to place

        kinds = []
        if ev.tracks.has_video():
            kinds.append("V")
        if ev.tracks.audio():
            kinds.append("A")
        if not kinds:
            raise AAFError(f"event at {format_tc(ev.rec_in, rate)} has no track")

        for kind in kinds:
            ends = occupied[kind]
            slot = None
            for i, last_end in enumerate(ends):
                if last_end <= start:
                    slot = i
                    break
            if slot is None:
                ends.append(0)
                slot = len(ends) - 1
            ends[slot] = end
            out.append(Placement(ev, slot, kind))

    return out


def _q(tag: str) -> str:
    return f"{{{AAF_NS}}}{tag}"


def write_aafx(el: EditList, title: str | None = None,
               audio_track_names: list[str] | None = None,
               video_track_name: str = "V1") -> str:
    """Serialise an edit list as AAFX 2.0 XML."""
    if el.rate is None:
        raise AAFError("list has no frame rate")
    rate = el.rate
    placements = place_clips(el)

    ET.register_namespace("", AAF_NS)
    ET.register_namespace("xml", XML_NS)

    root = ET.Element(_q("AAF"))
    root.set("version", "2.0")

    comp = ET.SubElement(root, _q("CompositionMob"))
    comp.set("MobID", str(uuid.uuid4()))
    comp.set("Name", title if title is not None else (el.title or "Untitled"))
    comp.set("LastModified", "2026-01-01T00:00:00Z")

    # group placements by track
    by_track: dict[tuple[str, int], list[Placement]] = {}
    for p in placements:
        by_track.setdefault((p.track_kind, p.track_index), []).append(p)

    for (kind, idx) in sorted(by_track):
        slot = ET.SubElement(comp, _q("TimelineMobSlot"))
        slot.set("SlotID", str(len(comp.findall(_q("TimelineMobSlot")))))
        slot.set("TrackKind", kind)

        if kind == "V":
            name = video_track_name
        else:
            names = audio_track_names or []
            name = names[idx] if idx < len(names) else f"A{idx + 1}"
        slot.set("TrackName", name)

        datadef = "Picture" if kind == "V" else "Sound"
        slot.set("DataDef", datadef)

        # emit clips and fillers in record order
        cursor = 0
        for p in sorted(by_track[(kind, idx)], key=lambda x: to_frames(x.event.rec_in, rate)):
            ev = p.event
            start = to_frames(ev.rec_in, rate)
            length = to_frames(ev.rec_out, rate) - start

            if start > cursor:
                filler = ET.SubElement(slot, _q("Filler"))
                filler.set("Length", str(start - cursor))

            clip = ET.SubElement(slot, _q("SourceClip"))
            clip.set("Start", str(start))
            clip.set("Length", str(length))
            clip.set("SourceMobName", ev.clip_name or ev.roll_name)
            clip.set("SourceMobSlotID", "1")
            clip.set("SourceStart", str(to_frames(ev.src_in, rate)))

            if ev.transition.kind != "C":
                og = ET.SubElement(clip, _q("OperationGroup"))
                og.set("Transition", ev.transition.name)
                og.set("Length", str(ev.transition.duration))

            cursor = start + length

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def read_aafx(xml_text: str, rate: Rate) -> EditList:
    """Minimal AAFX reader for round-trip verification."""
    root = ET.fromstring(xml_text)
    comp = root.find(_q("CompositionMob"))
    if comp is None:
        raise AAFError("no CompositionMob")

    el = EditList(rate=rate, title=comp.get("Name", ""))
    from .timecode import from_frames

    for slot in comp.findall(_q("TimelineMobSlot")):
        kind = slot.get("TrackKind")
        cursor = 0
        for node in slot:
            tag = node.tag.split("}")[-1]
            if tag == "Filler":
                cursor += int(node.get("Length", "0"))
                continue
            if tag != "SourceClip":
                continue
            start = int(node.get("Start", "0"))
            length = int(node.get("Length", "0"))
            src_start = int(node.get("SourceStart", "0"))

            from .model import Track, TrackSet
            tracks = TrackSet(frozenset({Track("V" if kind == "V" else "A",
                                              "V" if kind == "V" else "A")}))
            el.events.append(Event(
                rec_in=from_frames(start, rate),
                rec_out=from_frames(start + length, rate),
                src_in=from_frames(src_start, rate),
                src_out=from_frames(src_start + length, rate),
                roll_name=node.get("SourceMobName", ""),
                clip_name=node.get("SourceMobName", ""),
                tracks=tracks,
            ))
            cursor = start + length

    el.events.sort(key=lambda e: to_frames(e.rec_in, rate))
    el.renumber()
    return el
