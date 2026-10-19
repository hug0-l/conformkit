"""MIDI export and Update Data tests."""
import os
import struct

import pytest

from conformkit.midi import (
    MidiSettings, TICKS_PER_QUARTER, build_smf, frames_to_ticks, write_midi,
)
from conformkit.model import EditList, Event, Transition, parse_track_text
from conformkit.timecode import Timecode, add, rate_for, to_frames
from conformkit.update import (
    ChangeListEntry, UpdateError, UpdateSettings, apply_update,
    parse_change_list,
)

R25 = rate_for("25")
BASE = Timecode(0, 0, 0, 0)


def ev(roll="R1", clip="C1", start=0, dur=100, track="V"):
    return Event(
        rec_in=add(BASE, start, R25), rec_out=add(BASE, start + dur, R25),
        src_in=add(BASE, start + 1000, R25),
        src_out=add(BASE, start + dur + 1000, R25),
        roll_name=roll, clip_name=clip,
        tracks=parse_track_text(track), transition=Transition("C", 0),
    )


def el(events):
    return EditList(events=events, rate=R25, title="L")


# --------------------------------------------------------------- SMF basics

def test_smf_header():
    smf = build_smf(el([ev()]), R25)
    assert smf[:4] == b"MThd"
    length, fmt, ntrks, division = struct.unpack(">IHHH", smf[4:14])
    assert length == 6
    assert fmt == 1
    assert ntrks >= 2
    assert division == TICKS_PER_QUARTER


def test_smf_track_chunks_well_formed():
    smf = build_smf(el([ev()]), R25)
    pos = 14
    chunks = 0
    while pos < len(smf):
        assert smf[pos:pos + 4] == b"MTrk"
        size = struct.unpack(">I", smf[pos + 4:pos + 8])[0]
        pos += 8 + size
        chunks += 1
    assert pos == len(smf)
    assert chunks >= 2


def _track_bodies(smf):
    pos, out = 14, []
    while pos + 8 <= len(smf):
        n = struct.unpack(">I", smf[pos + 4:pos + 8])[0]
        out.append(smf[pos + 8:pos + 8 + n])
        pos += 8 + n
    return out


def _events(body):
    """Yield (absolute_tick, status, payload) from a track body."""
    i, tick = 0, 0
    while i < len(body):
        delta = 0
        while True:
            b = body[i]
            i += 1
            delta = (delta << 7) | (b & 0x7F)
            if not b & 0x80:
                break
        tick += delta
        status = body[i]
        if status == 0xFF:
            kind = body[i + 1]
            i += 2
            n = body[i]
            i += 1
            payload = body[i:i + n]
            i += n
            yield tick, 0xFF00 | kind, payload
        elif status in (0x90, 0x80):
            yield tick, status, body[i + 1:i + 3]
            i += 3
        else:
            raise AssertionError(f"unexpected status 0x{status:02x}")


def test_smf_ends_with_eot():
    smf = build_smf(el([ev()]), R25)
    tracks = _track_bodies(smf)
    assert tracks
    for body in tracks:
        assert body.endswith(b"\xff\x2f\x00")


def test_marker_track_has_names():
    smf = build_smf(el([ev(clip="SHOT_A")]), R25)
    assert b"SHOT_A" in smf


def test_note_events_present():
    smf = build_smf(el([ev()]), R25, MidiSettings(marker_track=False))
    assert b"\x90" in smf      # note on
    assert b"\x80" in smf      # note off


def test_settings_disable_tracks():
    only_markers = build_smf(
        el([ev()]), R25,
        MidiSettings(marker_track=True, note_track=False))
    ntrks = struct.unpack(">H", only_markers[10:12])[0]
    assert ntrks == 2          # conductor + markers


def test_ticks_are_monotonic():
    """Events must never produce a negative delta (VLQ cannot encode one)."""
    events = [ev(start=i * 200, roll=f"R{i}") for i in range(20)]
    smf = build_smf(el(events), R25)

    markers = [tick for body in _track_bodies(smf)
               for tick, status, _ in _events(body) if status == 0xFF06]
    assert len(markers) == 20
    assert all(b > a for a, b in zip(markers, markers[1:]))


def test_frames_to_ticks_scales():
    assert frames_to_ticks(0, R25) == 0
    one_second = frames_to_ticks(25, R25)
    two_seconds = frames_to_ticks(50, R25)
    assert two_seconds == pytest.approx(one_second * 2, rel=0.02)


def test_write_midi(tmp_path):
    p = tmp_path / "cues.mid"
    size = write_midi(str(p), el([ev()]), R25)
    assert p.exists()
    assert p.stat().st_size == size
    assert p.read_bytes()[:4] == b"MThd"


# --------------------------------------------------------------- update

def test_update_applies_new_destination():
    edit = el([ev(start=0), ev(start=200, roll="R2")])
    entries = [ChangeListEntry(event_number=1, new_rec_in=add(BASE, 50, R25),
                               new_rec_out=add(BASE, 150, R25))]
    result = apply_update(edit, R25, entries)
    assert result.applied == 1
    assert to_frames(edit.events[0].rec_in, R25) == 50


def test_update_leaves_unmentioned_events_alone():
    edit = el([ev(start=0), ev(start=200, roll="R2")])
    before = to_frames(edit.events[1].rec_in, R25)
    apply_update(edit, R25, [ChangeListEntry(event_number=1,
                                             new_rec_in=add(BASE, 50, R25),
                                             new_rec_out=add(BASE, 150, R25))])
    assert to_frames(edit.events[1].rec_in, R25) == before


def test_update_reports_unmatched():
    edit = el([ev()])
    result = apply_update(edit, R25, [ChangeListEntry(event_number=99)])
    assert result.skipped == 1
    assert result.messages


def test_update_deletes_tagged():
    edit = el([ev(start=0), ev(start=200, roll="R2")])
    entries = [ChangeListEntry(event_number=1, tag="DEL")]
    result = apply_update(edit, R25, entries,
                          UpdateSettings(delete_tagged=True, delete_tag="DEL"))
    assert result.deleted == 1
    assert len(edit.events) == 1


def test_update_stamps_version():
    edit = el([ev()])
    result = apply_update(
        edit, R25, [ChangeListEntry(event_number=1)],
        UpdateSettings(update_version_field=True,
                       version_field_name="Version", version_field_value="7"))
    assert result.version_stamped
    assert edit.events[0].user["Version"] == "7"


def test_update_is_transactional():
    """A failure mid-way must leave the list untouched."""
    edit = el([ev(start=0)])
    original = to_frames(edit.events[0].rec_in, R25)

    class Boom:
        def __iter__(self):
            yield ChangeListEntry(event_number=1,
                                  new_rec_in=add(BASE, 999, R25),
                                  new_rec_out=add(BASE, 1099, R25))
            raise RuntimeError("boom")

    with pytest.raises((UpdateError, RuntimeError)):
        apply_update(edit, R25, Boom())

    assert to_frames(edit.events[0].rec_in, R25) == original


def test_update_source_mode():
    edit = el([ev()])
    entries = [ChangeListEntry(event_number=1,
                               new_src_in=add(BASE, 5000, R25),
                               new_src_out=add(BASE, 5100, R25))]
    apply_update(edit, R25, entries,
                 UpdateSettings(update_timecodes_in=("source",)))
    assert to_frames(edit.events[0].src_in, R25) == 5000


def test_update_matches_by_number_not_roll():
    """A shared roll must not let one entry hit every event on that roll.

    Regression: matching on both axes meant the last entry with a common roll
    overwrote the first event repeatedly.
    """
    edit = el([ev(start=0, roll="AX"), ev(start=200, roll="AX"),
               ev(start=400, roll="AX")])
    entries = [
        ChangeListEntry(event_number=1, new_rec_in=add(BASE, 10, R25),
                        new_rec_out=add(BASE, 110, R25)),
        ChangeListEntry(event_number=2, new_rec_in=add(BASE, 210, R25),
                        new_rec_out=add(BASE, 310, R25)),
        ChangeListEntry(event_number=3, new_rec_in=add(BASE, 410, R25),
                        new_rec_out=add(BASE, 510, R25)),
    ]
    apply_update(edit, R25, entries,
                 UpdateSettings(update_timecodes_in=("destination",)))

    got = [to_frames(e.rec_in, R25) for e in edit.events]
    assert got == [10, 210, 410]


def test_update_roll_match_when_no_number():
    edit = el([ev(start=0, roll="AAA"), ev(start=200, roll="BBB")])
    apply_update(edit, R25,
                 [ChangeListEntry(roll="BBB",
                                  new_rec_in=add(BASE, 900, R25),
                                  new_rec_out=add(BASE, 1000, R25))],
                 UpdateSettings(update_timecodes_in=("destination",)))
    assert to_frames(edit.events[0].rec_in, R25) == 0
    assert to_frames(edit.events[1].rec_in, R25) == 900


def test_parse_change_list():
    text = ("EVENT\tROLL\tREC_IN\tREC_OUT\n"
            "1\tR1\t01:00:00:00\t01:00:04:00\n")
    entries = parse_change_list(text, R25)
    assert len(entries) == 1
    assert entries[0].event_number == 1
    assert entries[0].roll == "R1"
    assert entries[0].new_rec_in is not None


def test_parse_change_list_empty():
    assert parse_change_list("", R25) == []
    assert parse_change_list("EVENT\tROLL\n", R25) == []
