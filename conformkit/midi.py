"""Standard MIDI File writer (SMF type 1).

the reference tool exports MIDI so a DAW can follow picture cues: one track of markers, one
of notes, one of punches, one of streamers. The recovered settings vocabulary is
used as the configuration surface:

    ExpMIDITrkMarker, ExpMIDITrkNote, ExpMIDITrkPunch, ExpMIDITrkStreamer,
    ExpMIDITrkTextCue, ExpMIDINoteNo, ExpMIDINoteFrames,
    ExpMIDINoteDurnOfEvent, ExpMIDIPunchNo, ExpMIDIPunchFrames,
    ExpMIDIAddPullDown, ExpMIDIMarkerNameInd, ExpMIDIStreamColStr,
    ExpMIDIStreamLenStr, ExpMIDITextCueAdvSecStr

SMF is a published standard (MMA), so the container is written directly rather
than through a library.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = ["MidiSettings", "MidiEvent", "MidiTrack", "build_smf",
           "write_midi", "TICKS_PER_QUARTER"]


TICKS_PER_QUARTER = 960


@dataclass
class MidiSettings:
    """Which cue types to emit, and how."""

    marker_track: bool = True
    note_track: bool = True
    punch_track: bool = False
    streamer_track: bool = False
    text_cue_track: bool = False

    note_number: int = 60            # middle C for event cues
    note_frames: int = 25            # note length in frames
    note_duration_of_event: bool = False

    punch_number: int = 61
    punch_frames: int = 10

    marker_name_index: bool = True   # prefix marker names with the event number
    add_pull_down: bool = False

    streamer_channel: int = 0
    streamer_length_frames: int = 25
    text_cue_advance_seconds: float = 0.0


@dataclass
class MidiEvent:
    tick: int
    kind: str                        # "meta" | "note_on" | "note_off" | "text"
    data: bytes = b""


@dataclass
class MidiTrack:
    name: str = ""
    events: list[MidiEvent] = field(default_factory=list)


# ---------------------------------------------------------------- helpers

def _vlq(value: int) -> bytes:
    """MIDI variable-length quantity."""
    if value < 0:
        value = 0
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _meta(kind: int, payload: bytes) -> bytes:
    return b"\xFF" + bytes([kind]) + _vlq(len(payload)) + payload


def _track_chunk(track: MidiTrack) -> bytes:
    body = bytearray()

    if track.name:
        body += _vlq(0) + _meta(0x03, track.name.encode("utf-8", "replace"))

    last = 0
    for ev in sorted(track.events, key=lambda e: e.tick):
        if ev.tick < 0:
            raise ValueError("midi event tick cannot be negative")
        delta = max(0, ev.tick - last)
        last = ev.tick
        body += _vlq(delta)

        if ev.kind == "meta":
            body += ev.data
        elif ev.kind == "text":
            body += _meta(0x01, ev.data)
        elif ev.kind == "note_on":
            body += ev.data
        elif ev.kind == "note_off":
            body += ev.data
        else:
            raise ValueError(f"unknown midi event kind {ev.kind!r}")

    body += _vlq(0) + _meta(0x2F, b"")     # end of track
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def frames_to_ticks(frames: int, rate) -> int:
    """Convert frames to MIDI ticks using the true (real-time) frame rate."""
    if frames <= 0:
        return 0
    seconds = frames / float(rate.actual)
    beats = seconds * 2.0                     # 120 bpm
    return int(round(beats * TICKS_PER_QUARTER))


# ---------------------------------------------------------------- build

def build_smf(edit_list, rate, settings: MidiSettings | None = None) -> bytes:
    """Build a type-1 SMF from an edit list."""
    settings = settings or MidiSettings()

    for label, value in (("note_number", settings.note_number),
                         ("punch_number", settings.punch_number)):
        if not 0 <= int(value) <= 127:
            raise ValueError(f"{label} must be 0..127, got {value!r}")
    if not 0 <= int(settings.streamer_channel) <= 15:
        raise ValueError("streamer_channel must be 0..15")

    tracks: list[MidiTrack] = []

    # tempo + time signature on the conductor track
    conductor = MidiTrack(name="conductor")
    conductor.events.append(MidiEvent(0, "meta",
                                      _meta(0x51, b"\x07\xA1\x20")))   # 120 bpm
    conductor.events.append(MidiEvent(0, "meta",
                                      _meta(0x58, b"\x04\x02\x18\x08")))  # 4/4
    tracks.append(conductor)

    def track_ticks(ev) -> int:
        return frames_to_ticks(_frames(ev.rec_in, rate), rate)

    if settings.marker_track:
        tr = MidiTrack(name="markers")
        for i, ev in enumerate(edit_list, start=1):
            name = ev.clip_name or ev.roll_name
            if settings.marker_name_index:
                name = f"{i:03d} {name}"
            tr.events.append(MidiEvent(track_ticks(ev), "meta",
                                       _meta(0x06, name.encode("utf-8", "replace"))))
        tracks.append(tr)

    if settings.note_track:
        tr = MidiTrack(name="cues")
        for ev in edit_list:
            start = track_ticks(ev)
            if settings.note_duration_of_event:
                # the event's own length, not its absolute position
                span = (_frames(ev.rec_out, rate) - _frames(ev.rec_in, rate))
            else:
                span = settings.note_frames
            length = frames_to_ticks(max(1, span), rate)
            tr.events.append(MidiEvent(start, "note_on",
                                       bytes([0x90, settings.note_number, 100])))
            tr.events.append(MidiEvent(start + length, "note_off",
                                       bytes([0x80, settings.note_number, 0])))
        tracks.append(tr)

    if settings.punch_track:
        tr = MidiTrack(name="punches")
        for ev in edit_list:
            start = track_ticks(ev)
            length = frames_to_ticks(max(1, settings.punch_frames), rate)
            tr.events.append(MidiEvent(start, "note_on",
                                       bytes([0x90, settings.punch_number, 110])))
            tr.events.append(MidiEvent(start + length, "note_off",
                                       bytes([0x80, settings.punch_number, 0])))
        tracks.append(tr)

    if settings.streamer_track:
        tr = MidiTrack(name="streamers")
        for ev in edit_list:
            length = frames_to_ticks(max(1, settings.streamer_length_frames), rate)
            start = max(0, track_ticks(ev) - length)
            tr.events.append(MidiEvent(start, "note_on",
                                       bytes([0x90 | (settings.streamer_channel & 0x0F),
                                              60, 90])))
            tr.events.append(MidiEvent(track_ticks(ev), "note_off",
                                       bytes([0x80 | (settings.streamer_channel & 0x0F),
                                              60, 0])))
        tracks.append(tr)

    if settings.text_cue_track:
        tr = MidiTrack(name="text cues")
        adv = int(settings.text_cue_advance_seconds * 2 * TICKS_PER_QUARTER)
        for i, ev in enumerate(edit_list, start=1):
            text = f"{i:03d} {ev.clip_name or ev.roll_name}"
            tr.events.append(MidiEvent(max(0, track_ticks(ev) - adv), "text",
                                       text.encode("utf-8", "replace")))
        tracks.append(tr)

    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(tracks), TICKS_PER_QUARTER)
    return header + b"".join(_track_chunk(t) for t in tracks)


def _frames(tc, rate) -> int:
    from .timecode import to_frames
    return to_frames(tc, rate)


def write_midi(path: str, edit_list, rate,
               settings: MidiSettings | None = None) -> int:
    data = build_smf(edit_list, rate, settings)
    with open(path, "wb") as fh:
        fh.write(data)
    return len(data)
