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


def _track_by_name(smf, name):
    """Return the events of the track whose FF 03 name event matches."""
    for body in _track_bodies(smf):
        events = list(_events(body))
        if events and events[0][1] == 0xFF03 and events[0][2] == name.encode():
            return events
    raise AssertionError(f"no track named {name!r}")


def _named_ticks(smf, name, status):
    return [tick for tick, kind, _ in _track_by_name(smf, name)
            if kind == status]


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


# --------------------------------------------------------------- text cues

def test_text_cue_track_emits_text_events():
    events = [ev(clip="SHOT_A", start=0), ev(clip="SHOT_B", start=250)]
    smf = build_smf(el(events), R25,
                    MidiSettings(note_track=False, text_cue_track=True))

    cues = _track_by_name(smf, "text cues")
    texts = [(tick, payload.decode()) for tick, kind, payload in cues
             if kind == 0xFF01]

    assert texts == [(0, "001 SHOT_A"), (19200, "002 SHOT_B")]
    assert all(kind != 0xFF06 for _, kind, _ in cues)   # text, not markers


def test_text_cue_prefix_follows_marker_name_index():
    smf = build_smf(el([ev(clip="SHOT_A")]), R25,
                    MidiSettings(note_track=False, text_cue_track=True,
                                 marker_name_index=False))
    texts = [payload.decode()
             for _, kind, payload in _track_by_name(smf, "text cues")
             if kind == 0xFF01]
    assert texts == ["SHOT_A"]


def test_text_cue_advance_moves_earlier_and_clamps():
    events = [ev(clip=f"C{i}", start=start)
              for i, start in enumerate((0, 25, 250), start=1)]
    smf = build_smf(el(events), R25,
                    MidiSettings(note_track=False, text_cue_track=True,
                                 text_cue_advance_seconds=2.5))

    # 2.5 s at 120 bpm / 960 ticks per quarter is 4800 ticks.
    assert _named_ticks(smf, "text cues", 0xFF01) == [0, 0, 14400]


def test_negative_text_cue_advance_is_rejected():
    with pytest.raises(ValueError, match="text_cue_advance_seconds"):
        build_smf(el([ev()]), R25,
                  MidiSettings(text_cue_advance_seconds=-0.1))


# --------------------------------------------------------------- pull-down

def test_pull_down_shortens_marker_ticks():
    settings = dict(note_track=False)
    plain = build_smf(el([ev(start=250)]), R25, MidiSettings(**settings))
    pulled = build_smf(el([ev(start=250)]), R25,
                       MidiSettings(add_pull_down=True, **settings))

    plain_tick = _named_ticks(plain, "markers", 0xFF06)[0]
    pulled_tick = _named_ticks(pulled, "markers", 0xFF06)[0]

    assert plain_tick == 19200                       # 10 s at 1920 ticks/s
    assert pulled_tick == 19181                      # round(19200 * 1000/1001)
    assert pulled_tick == round(plain_tick * 1000 / 1001)


def test_pull_down_applies_to_every_generated_track():
    kwargs = dict(marker_track=True, note_track=True, punch_track=True,
                  streamer_track=True, text_cue_track=True,
                  note_duration_of_event=True, marker_name_index=False)
    events = [ev(clip="SHOT", start=250, dur=50)]
    plain = build_smf(el(events), R25, MidiSettings(**kwargs))
    pulled = build_smf(el(events), R25,
                       MidiSettings(add_pull_down=True, **kwargs))

    for name in ("markers", "cues", "punches", "streamers", "text cues"):
        # compare that track's ticks with the same track in the pulled file
        plain_track = _track_by_name(plain, name)
        pulled_track = _track_by_name(pulled, name)
        assert len(plain_track) == len(pulled_track)
        for (plain_tick, plain_kind, _), (pulled_tick, pulled_kind, _) in zip(
                plain_track, pulled_track):
            assert plain_kind == pulled_kind
            scaled = plain_tick * 1000 / 1001
            assert pulled_tick <= plain_tick
            assert pulled_tick == pytest.approx(scaled, abs=1.0)


def test_disabled_settings_emit_no_text_cue_track():
    smf = build_smf(el([ev()]), R25)

    names = [list(_events(body))[0][2] for body in _track_bodies(smf)]
    assert b"text cues" not in names
    assert all(kind != 0xFF01 for body in _track_bodies(smf)
               for _, kind, _ in _events(body))


def test_disabled_pull_down_leaves_marker_ticks_unchanged():
    events = [ev(start=250)]
    default = build_smf(el(events), R25, MidiSettings(note_track=False))
    explicit = build_smf(el(events), R25,
                         MidiSettings(note_track=False, add_pull_down=False))
    assert (_named_ticks(default, "markers", 0xFF06)
            == _named_ticks(explicit, "markers", 0xFF06) == [19200])


def test_text_cue_advance_combines_with_pull_down():
    smf = build_smf(el([ev(clip="SHOT", start=250)]), R25,
                    MidiSettings(note_track=False, text_cue_track=True,
                                 text_cue_advance_seconds=1.0,
                                 add_pull_down=True))

    marker = _named_ticks(smf, "markers", 0xFF06)[0]
    cue = _named_ticks(smf, "text cues", 0xFF01)[0]

    assert marker == 19181
    assert cue == marker - 1920      # a one-second advance, not itself pulled
    assert cue == 17261


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
