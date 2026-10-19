"""AAF-XML v1.1 interchange, plus binary AAF identification.

`write_aafx` emits the XML stored form defined by the AAF SDK, not a private
schema. The element names, the namespace and the fixed meta-dictionary
identifiers below are taken from the AAF reference implementation
(`ref-impl/xsd/aafxml`): the example document `example/example.xml`, the
generated schema `example/aaf.xsd` and the baseline dictionary
`metadict-20050803.xml`. Nothing here is invented: reading a field name out of
those files is what establishes that an AAF reader will recognise it.

The document shape is:

    AAF
      Header
        HeaderDictionary -> Dictionary -> DataDefinitions
        ByteOrder, LastModified
        Content
          ContentStorage
            Mobs
              CompositionMob
                TimelineMobSlot per (EDL track label, overlap lane)
                  Sequence -> Filler / SourceClip
              SourceMob per (source name, media kind)
                TimelineMobSlot -> Sequence -> Filler
                EssenceDescription -> PCMDescriptor / CDCIDescriptor
        Version, IdentificationList, ObjectModelVersion

Mob and source identifiers are UMIDs in the form the schema's `MobIDType`
accepts (`urn:x-umid:<24 hex>-13-<6 hex>-<32 hex>`). They are derived with
SHA-256 from stable inputs (title, rate, event geometry, source name, media
kind), so re-exporting the same edit list produces byte-identical XML. UUID
URNs would be rejected by that schema type, so they are not used for MobIDs.

Track identity: `place_clips` keeps the EDL label of every track an event is
selected on (`V`, `A1`, `A2`, ...) and only opens an extra lane when two clips
overlap on the same label. The label is written to `SlotName`; `A` is
normalised to `A1` because an EDL `A` column is channel 1. A1 and A2 are never
merged into one placement.

What this format does not carry:

  * Transitions. An AAF `Transition` must reference an `OperationGroup` and an
    `OperationDefinition`; those definitions are file extensions, not part of
    the baseline dictionary emitted here. Inventing an identifier would make
    the document invalid, so dissolve/wipe/key metadata is not serialised.
    Clips keep their record and source timing; `read_aafx` returns cuts.
  * Essence. The document describes the edit and carries descriptors, but no
    media. Source-mob slots use timeline edit units (frames) so `StartTime`
    round-trips exactly; no sample-accurate audio offsets are implied.
  * The old private conformkit schema. `read_aafx` now reads only AAF-XML v1.1;
    documents written by conformkit before this change are not readable.

AAF-XML is the AAF SDK's XML stored format, not a DAW interchange file. Binary
AAF (see `aafwrite`) is the interoperable route for applications that import
AAF. This module makes no claim that an NLE or DAW will import the XML.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from . import __version__
from .model import EditList, Event, Track, TrackSet
from .timecode import Rate, format_tc, from_frames, to_frames

__all__ = ["AAFError", "place_clips", "write_aafx", "read_aafx", "AAF_NS",
           "identify_aaf", "read_aaf_names", "AafContainer"]

AAF_NS = "http://www.aafassociation.org/aafx/v1.1/20050628"
AAF_NS_A = AAF_NS                     # retained name for existing importers
XML_NS = "http://www.w3.org/XML/1998/namespace"

# Fixed for reproducible output. The schema accepts any xs:dateTime with a
# zone, or the explicit "zero" timestamp used by the AAF SDK.
_TIMESTAMP = "1970-01-01T00:00:00.00Z"
_PRODUCT_VERSION = __version__

# UMID prefix from the reference example: 12-byte SMPTE universal label,
# length 0x13, zero instance number. The remaining 16 bytes are material.
_UMID_PREFIX = "urn:x-umid:060c2b340205110101041000-13-000000-"

# (uid, identification, name, description). Identifications are copied from
# the dictionary in the reference example, not guessed.
_DATA_DEFS = (
    ("DataDef_Picture",
     "urn:x-ul:060e2b34.0401.0101.01030202.01000000",
     "Picture", "Picture data"),
    ("DataDef_Sound",
     "urn:x-ul:060e2b34.0401.0101.01030202.02000000",
     "Sound", "Sound data"),
    ("DataDef_LegacyPicture",
     "urn:uuid:6f3c8ce1-6cef-11d2-807d-006008143e6f",
     "Picture", "Picture data (legacy)"),
    ("DataDef_LegacySound",
     "urn:uuid:78e1ebe1-6cef-11d2-807d-006008143e6f",
     "Sound", "Sound data (legacy)"),
    ("DataDef_Timecode",
     "urn:x-ul:060e2b34.0401.0101.01030201.01000000",
     "Timecode", "Timecode data"),
)

_PRODUCT_ID = "urn:uuid:" + str(uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/hug0-l/conformkit"))
_GENERATION_AUID = "urn:uuid:" + str(uuid.uuid5(
    uuid.NAMESPACE_URL, "https://github.com/hug0-l/conformkit/aafxml"))


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


# --------------------------------------------------------------- placement

@dataclass
class Placement:
    """One event on one physical slot.

    `track_label` is the EDL label the event selected (`V`, `A1`, `A2`, ...).
    `lane` is zero unless overlapping clips forced a second slot for the same
    label. `track_index` is the 0-based slot number within the media kind and
    exists for callers that only reason about video/audio lanes.
    """

    event: Event
    track_index: int
    track_kind: str
    track_label: str = ""
    lane: int = 0


def _canonical_label(track: Track) -> str:
    """Return the slot name for a track, preserving its EDL identity.

    An EDL `A` column is audio channel 1, so it is written as `A1`. Every
    other label (`V`, `A2`, `AA`, `B`) is kept verbatim.
    """
    if track.kind == "A" and track.label == "A":
        return "A1"
    return track.label


def _natural_key(text: str):
    """Sort A1 < A2 < A10 without mixing int and str comparisons."""
    parts = re.split(r"(\d+)", text.lower())
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts)


def _source_name(ev: Event) -> str:
    return ev.source_file or ev.clip_name or ev.roll_name or "source"


def _event_key(ev: Event, rate: Rate):
    """Full ordering key, so placement does not depend on input list order."""
    return (to_frames(ev.rec_in, rate), to_frames(ev.rec_out, rate),
            to_frames(ev.src_in, rate), _source_name(ev),
            ",".join(str(t) for t in ev.tracks))


def place_clips(el: EditList) -> list[Placement]:
    """Assign each event to every track label it selected.

    Zero-length events are skipped: they are transition anchors carrying no
    media. Deterministic: events are processed in record order, and within one
    EDL label a clip goes to the lowest lane whose last end is <= its record
    start. A new lane is opened only when every lane for that label overlaps.

    Unlike the previous implementation this does not merge the EDL labels: an
    event on `A2` stays on `A2` even when `A1` is free, and an event selected
    on both video and audio yields one placement for each.
    """
    if el.rate is None:
        raise AAFError("list has no frame rate")
    rate = el.rate

    # (kind, label) -> last occupied end frame for each lane
    occupied: dict[tuple[str, str], list[int]] = {}
    out: list[Placement] = []

    for ev in sorted(el.events, key=lambda e: _event_key(e, rate)):
        start = to_frames(ev.rec_in, rate)
        end = to_frames(ev.rec_out, rate)
        if end < start:
            raise AAFError(
                f"event at {format_tc(ev.rec_in, rate)} has negative duration")
        if end == start:
            continue        # transition anchor, no media to place

        targets: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for track in ev.tracks:
            target = (track.kind, _canonical_label(track))
            if target not in seen:
                seen.add(target)
                targets.append(target)
        if not targets:
            raise AAFError(f"event at {format_tc(ev.rec_in, rate)} has no track")

        for kind, label in targets:
            key = (kind, label)
            ends = occupied.setdefault(key, [])
            lane = None
            for i, last_end in enumerate(ends):
                if last_end <= start:
                    lane = i
                    break
            if lane is None:
                ends.append(0)
                lane = len(ends) - 1
            ends[lane] = end
            out.append(Placement(ev, -1, kind, label, lane))

    # Number the resulting slots per media kind, in a stable order.
    slots = sorted({(p.track_kind, p.track_label, p.lane) for p in out},
                   key=lambda s: (0 if s[0] == "V" else 1,
                                  _natural_key(s[1]), s[2]))
    counters = {"V": 0, "A": 0}
    index: dict[tuple[str, str, int], int] = {}
    for kind, label, lane in slots:
        index[(kind, label, lane)] = counters[kind]
        counters[kind] += 1
    for p in out:
        p.track_index = index[(p.track_kind, p.track_label, p.lane)]

    return out


# --------------------------------------------------------------- write

def _q(tag: str) -> str:
    return f"{{{AAF_NS}}}{tag}"


def _sub(parent, tag: str):
    return ET.SubElement(parent, _q(tag))


def _text(parent, tag: str, value) -> None:
    node = _sub(parent, tag)
    node.text = str(value)


def _umid(key: str) -> str:
    """Derive a schema-valid, reproducible UMID from a stable string."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return _UMID_PREFIX + digest


def _rate_text(rate: Rate) -> str:
    actual = rate.actual
    return f"{actual.numerator}/{actual.denominator}"


def _component_def(kind: str) -> str:
    return "DataDef_Picture" if kind == "V" else "DataDef_Sound"


def _slot_name(kind: str, label: str, lane: int,
               audio_track_names: list[str] | None,
               video_track_name: str | None,
               audio_ordinal: int) -> str:
    if kind == "V" and video_track_name:
        base = video_track_name
    elif kind == "A" and audio_track_names and audio_ordinal < len(
            audio_track_names):
        base = audio_track_names[audio_ordinal]
    else:
        base = label
    return base if lane == 0 else f"{base}.{lane + 1}"


def _write_header(root):
    header = _sub(root, "Header")
    dictionary = _sub(_sub(header, "HeaderDictionary"), "Dictionary")
    defs = _sub(dictionary, "DataDefinitions")
    for uid, identification, name, description in _DATA_DEFS:
        definition = _sub(defs, "DataDefinition")
        definition.set("aaf:uid", uid)
        _text(definition, "DefinitionObjectIdentification", identification)
        _text(definition, "DefinitionObjectName", name)
        _text(definition, "DefinitionObjectDescription", description)

    _text(header, "ByteOrder", "LittleEndian")
    _text(header, "LastModified", _TIMESTAMP)
    return header


def _write_identification(header) -> None:
    ident = _sub(_sub(header, "IdentificationList"), "Identification")
    _text(ident, "CompanyName", "conformkit contributors")
    _text(ident, "ProductName", "conformkit")
    _text(ident, "ProductVersionString", _PRODUCT_VERSION)
    _text(ident, "ProductID", _PRODUCT_ID)
    _text(ident, "Date", _TIMESTAMP)
    _text(ident, "IdentificationPlatform", "Python")
    _text(ident, "GenerationAUID", _GENERATION_AUID)


def _write_source_mob(mobs, name: str, kind: str, extent: int,
                      mob_id: str, rate: Rate, rate_text: str,
                      sample_rate: int) -> None:
    mob = _sub(mobs, "SourceMob")
    _text(mob, "MobID", mob_id)
    _text(mob, "MobName", name)

    slots = _sub(mob, "MobSlots")
    slot = _sub(slots, "TimelineMobSlot")
    _text(slot, "SlotID", 1)
    _text(slot, "SlotName", name)

    sequence = _sub(_sub(slot, "MobSlotSegment"), "Sequence")
    _text(sequence, "ComponentDataDefinition", _component_def(kind))
    _text(sequence, "ComponentLength", extent)
    components = _sub(sequence, "Components")
    filler = _sub(components, "Filler")
    _text(filler, "ComponentDataDefinition", _component_def(kind))
    _text(filler, "ComponentLength", extent)

    _text(slot, "PhysicalTrackNumber", 1)
    _text(slot, "TimelineMobSlotEditRate", rate_text)
    _text(slot, "SlotOrigin", 0)

    _text(mob, "MobLastModified", _TIMESTAMP)
    _text(mob, "CreationTime", _TIMESTAMP)

    descriptor = _sub(_sub(mob, "EssenceDescription"),
                      "CDCIDescriptor" if kind == "V" else "PCMDescriptor")
    if kind == "V":
        _text(descriptor, "FileDescriptorSampleRate", rate_text)
        _text(descriptor, "FileDescriptorLength", extent)
        _text(descriptor, "StoredHeight", 1080)
        _text(descriptor, "StoredWidth", 1920)
        _text(descriptor, "FrameLayout", "FullFrame")
        lines = _sub(descriptor, "VideoLineMap")
        _text(lines, "Int32", 21)
        _text(lines, "Int32", 584)
        _text(descriptor, "ImageAspectRatio", "16/9")
        _text(descriptor, "ComponentWidth", 8)
        _text(descriptor, "HorizontalSubsampling", 2)
        _text(descriptor, "VerticalSubsampling", 1)
    else:
        # A PCM descriptor's length is in samples, not timeline frames.
        samples = round(extent * sample_rate / rate.actual)
        _text(descriptor, "FileDescriptorSampleRate",
              f"{sample_rate}/1")
        _text(descriptor, "FileDescriptorLength", samples)
        _text(descriptor, "AudioSamplingRate", f"{sample_rate}/1")
        _text(descriptor, "Channels", 1)
        _text(descriptor, "QuantizationBits", 24)
        _text(descriptor, "BlockAlign", 3)
        _text(descriptor, "AverageBPS", sample_rate * 3)


def _write_composition_mob(mobs, el: EditList, placements: list[Placement],
                           sources: dict[tuple[str, str], str],
                           title: str, rate_text: str,
                           audio_track_names: list[str] | None,
                           video_track_name: str | None) -> None:
    comp = _sub(mobs, "CompositionMob")

    signature = "|".join(
        f"{to_frames(e.rec_in, el.rate)}:{to_frames(e.rec_out, el.rate)}:"
        f"{to_frames(e.src_in, el.rate)}:{_source_name(e)}:"
        f"{','.join(str(t) for t in e.tracks)}"
        for e in sorted(el.events, key=lambda e: _event_key(e, el.rate)))
    comp_id = _umid(f"composition:{title}:{rate_text}:{signature}")
    _text(comp, "MobID", comp_id)
    _text(comp, "MobName", title)

    grouped: dict[tuple[str, str, int], list[Placement]] = {}
    for placement in placements:
        key = (placement.track_kind, placement.track_label, placement.lane)
        grouped.setdefault(key, []).append(placement)
    slots = sorted(grouped, key=lambda s: (0 if s[0] == "V" else 1,
                                           _natural_key(s[1]), s[2]))

    mob_slots = _sub(comp, "MobSlots")
    audio_ordinal = 0
    for slot_id, key in enumerate(slots, start=1):
        kind, label, lane = key
        slot = _sub(mob_slots, "TimelineMobSlot")
        _text(slot, "SlotID", slot_id)
        name = _slot_name(kind, label, lane, audio_track_names,
                          video_track_name, audio_ordinal)
        _text(slot, "SlotName", name)
        if kind == "A":
            audio_ordinal += 1

        members = sorted(
            grouped[key],
            key=lambda p: (to_frames(p.event.rec_in, el.rate),
                           to_frames(p.event.rec_out, el.rate),
                           _source_name(p.event)))
        total = max(to_frames(p.event.rec_out, el.rate) for p in members)

        sequence = _sub(_sub(slot, "MobSlotSegment"), "Sequence")
        _text(sequence, "ComponentDataDefinition", _component_def(kind))
        _text(sequence, "ComponentLength", total)
        components = _sub(sequence, "Components")

        cursor = 0
        for placement in members:
            ev = placement.event
            start = to_frames(ev.rec_in, el.rate)
            length = to_frames(ev.rec_out, el.rate) - start
            if start > cursor:
                filler = _sub(components, "Filler")
                _text(filler, "ComponentDataDefinition", _component_def(kind))
                _text(filler, "ComponentLength", start - cursor)
            elif start < cursor:
                raise AAFError(
                    f"internal placement overlap at "
                    f"{format_tc(ev.rec_in, el.rate)} on {label}")

            clip = _sub(components, "SourceClip")
            _text(clip, "ComponentDataDefinition", _component_def(kind))
            _text(clip, "ComponentLength", length)
            _text(clip, "SourceID",
                  sources[(_source_name(ev), kind)])
            _text(clip, "SourceMobSlotID", 1)
            _text(clip, "StartTime", to_frames(ev.src_in, el.rate))
            cursor = start + length

        _text(slot, "PhysicalTrackNumber", slot_id)
        _text(slot, "TimelineMobSlotEditRate", rate_text)
        _text(slot, "SlotOrigin", 0)

    _text(comp, "MobLastModified", _TIMESTAMP)
    _text(comp, "CreationTime", _TIMESTAMP)


def write_aafx(el: EditList, title: str | None = None,
               audio_track_names: list[str] | None = None,
               video_track_name: str | None = None,
               sample_rate: int = 48000) -> str:
    """Serialise an edit list as AAF-XML v1.1.

    `audio_track_names` overrides the slot names of audio tracks in slot
    order; without it the EDL labels are preserved. `video_track_name`
    overrides every video slot name. `sample_rate` is descriptor metadata
    only: no audio is embedded, and source slots stay in timeline frames.

    Transitions are intentionally not serialised; see the module docstring.
    """
    if el.rate is None:
        raise AAFError("list has no frame rate")
    rate = el.rate
    placements = place_clips(el)
    track_title = title if title is not None else (el.title or "Untitled")
    rate_text = _rate_text(rate)

    # One SourceMob per (source name, media kind). The extent is the furthest
    # point any clip reads, so a clip never addresses past the mob's end.
    extents: dict[tuple[str, str], int] = {}
    for placement in placements:
        ev = placement.event
        start = to_frames(ev.rec_in, rate)
        length = to_frames(ev.rec_out, rate) - start
        key = (_source_name(ev), placement.track_kind)
        extent = to_frames(ev.src_in, rate) + length
        extents[key] = max(extents.get(key, 0), extent)

    source_ids = {
        key: _umid(f"source:{key[1]}:{key[0]}") for key in extents
    }

    # AAF-XML uses a default namespace plus a prefixed attribute on dictionary
    # entries. ElementTree keeps one prefix per URI, so the element prefix is
    # the default and `xmlns:aaf` is declared literally on the root; the
    # `aaf:uid` attributes below are written under that declaration.
    ET.register_namespace("", AAF_NS)
    root = ET.Element(_q("AAF"))
    root.set("version", "1.1")
    root.set("xmlns:aaf", AAF_NS)

    header = _write_header(root)

    content = _sub(header, "Content")
    storage = _sub(content, "ContentStorage")
    mobs = _sub(storage, "Mobs")

    _write_composition_mob(mobs, el, placements, source_ids, track_title,
                           rate_text, audio_track_names, video_track_name)

    for key in sorted(extents, key=lambda k: (0 if k[1] == "V" else 1,
                                              _natural_key(k[0]))):
        name, kind = key
        _write_source_mob(mobs, name, kind, extents[key], source_ids[key],
                          rate, rate_text, sample_rate)

    _text(header, "Version", "1.1")
    _write_identification(header)
    _text(header, "ObjectModelVersion", 1)

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


# --------------------------------------------------------------- read

def _read_text(parent, tag: str) -> str:
    node = parent.find(_q(tag))
    return (node.text or "").strip() if node is not None else ""


def _read_int(parent, tag: str, default: int = 0) -> int:
    text = _read_text(parent, tag)
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        raise AAFError(f"{tag} is not an integer: {text!r}") from None


def _read_kind(data_definition: str) -> str:
    return "A" if data_definition.endswith("Sound") else "V"


def _read_track(slot_name: str, kind: str, audio_ordinal: int) -> Track:
    if kind == "V":
        return Track("V", "V")
    base = re.sub(r"\.\d+$", "", slot_name).strip()
    if not base:
        # No slot name: use the audio slot's position.
        return Track("A", f"A{audio_ordinal}")
    if base == "A":
        base = "A1"
    # Preserve the slot name even when it is a mixer's label rather than an
    # EDL column; the model's Track label is free-form.
    return Track("A", base)


def read_aafx(xml_text: str, rate: Rate) -> EditList:
    """Read the AAF-XML written by :func:`write_aafx`.

    Track names come from `TimelineMobSlot/SlotName` and are restored to the
    event's `TrackSet`; `A1` and `A2` remain distinct. Only AAF-XML v1.1 is
    accepted (the old private conformkit schema is not).

    Transitions are not restored: the writer does not serialise them (see the
    module docstring), so every event comes back as a cut. Source names are
    resolved through each `SourceClip/SourceID` to the referenced `SourceMob`'s
    `MobName`.
    """
    root = ET.fromstring(xml_text)
    if root.tag != _q("AAF"):
        raise AAFError(
            f"not AAF-XML v1.1: root element is {root.tag!r}")
    version = (root.get("version") or "").strip()
    if version != "1.1":
        raise AAFError(f"unsupported AAF-XML version {version!r}")

    comp = root.find(f".//{_q('CompositionMob')}")
    if comp is None:
        raise AAFError("no CompositionMob")

    source_names: dict[str, str] = {}
    for mob in root.findall(f".//{_q('SourceMob')}"):
        mob_id = _read_text(mob, "MobID")
        if mob_id:
            source_names[mob_id] = _read_text(mob, "MobName")

    el = EditList(rate=rate, title=_read_text(comp, "MobName"))
    audio_ordinal = 1
    mob_slots = comp.find(_q("MobSlots"))
    slots = mob_slots.findall(_q("TimelineMobSlot")) \
        if mob_slots is not None else []
    for slot in slots:
        segment = slot.find(_q("MobSlotSegment"))
        sequence = segment.find(_q("Sequence")) if segment is not None else None
        if sequence is None:
            continue

        kind = _read_kind(_read_text(sequence, "ComponentDataDefinition"))
        track = _read_track(_read_text(slot, "SlotName"), kind, audio_ordinal)
        if kind == "A":
            audio_ordinal += 1
        tracks = TrackSet(frozenset({track}))

        components = sequence.find(_q("Components"))
        if components is None:
            continue
        cursor = 0
        for node in components:
            tag = node.tag.split("}")[-1]
            length = _read_int(node, "ComponentLength")
            if tag == "Filler":
                cursor += length
                continue
            if tag != "SourceClip":
                # Transitions and other components still consume timeline
                # space, so later clips keep their record positions.
                cursor += length
                continue

            source_id = _read_text(node, "SourceID")
            source_name = source_names.get(source_id, "")
            src_start = _read_int(node, "StartTime")
            el.events.append(Event(
                rec_in=from_frames(cursor, rate),
                rec_out=from_frames(cursor + length, rate),
                src_in=from_frames(src_start, rate),
                src_out=from_frames(src_start + length, rate),
                roll_name=source_name,
                clip_name=source_name,
                tracks=tracks,
            ))
            cursor += length

    el.events.sort(key=lambda e: (
        to_frames(e.rec_in, rate),
        0 if e.tracks.has_video() else 1,
        ",".join(str(t) for t in e.tracks),
    ))
    el.renumber()
    return el
