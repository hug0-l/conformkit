"""EDL round-trip and negative tests."""
import pytest

from conformkit.edl import (
    DIALECTS, EDLError, detect_dialect, frame_rate_hint, parse_edl, write_edl,
)
from conformkit.model import Event, TrackSet, Transition, parse_track_text
from conformkit.timecode import Timecode, parse_tc, rate_for

R25 = rate_for("25")
DF2997 = rate_for("29.97", df=True)
NDF2997 = rate_for("29.97")


def tc(h, m, s, f=0):
    return Timecode(h, m, s, f)


def make_event(num, roll="R001", track="V", clip="CLIP1", start=0, dur=100, rate=R25):
    from conformkit.timecode import add
    base = Timecode(0, 0, 0, 0)
    rec_in = add(base, start, rate)
    rec_out = add(base, start + dur, rate)
    src_in = add(base, start + 1000, rate)
    src_out = add(base, start + dur + 1000, rate)
    return Event(
        rec_in=rec_in, rec_out=rec_out,
        src_in=src_in, src_out=src_out,
        roll_name=roll, clip_name=clip,
        tracks=parse_track_text(track),
        transition=Transition("C", 0),
        number=num,
    )


# --------------------------------------------------------------- round trip

@pytest.mark.parametrize("dialect", ["CMX3600", "File16", "File32", "File129"])
def test_roundtrip_basic(dialect):
    from conformkit.model import EditList
    el = EditList(events=[make_event(i + 1) for i in range(20)],
                  rate=R25, title="TEST")
    text = write_edl(el, dialect)
    back = parse_edl(text, R25, dialect)
    assert len(back) == len(el)
    for a, b in zip(el, back):
        assert a.rec_in == b.rec_in
        assert a.rec_out == b.rec_out
        assert a.src_in == b.src_in
        assert a.src_out == b.src_out
        assert a.roll_name == b.roll_name
        assert a.clip_name == b.clip_name
        assert set(a.tracks.tracks) == set(b.tracks.tracks)
        assert a.transition == b.transition


@pytest.mark.parametrize("track_text", ["V", "A", "A2", "A3", "AA", "B", "A2/V", "V/A"])
def test_roundtrip_all_tracks(track_text):
    from conformkit.model import EditList
    el = EditList(events=[make_event(1, track=track_text)], rate=R25)
    text = write_edl(el)
    back = parse_edl(text, R25)
    assert set(back.events[0].tracks.tracks) == set(el.events[0].tracks.tracks)


@pytest.mark.parametrize("kind,dur", [("D", 25), ("W", 12), ("K", 1)])
def test_roundtrip_transitions(kind, dur):
    from conformkit.model import EditList
    ev = make_event(1)
    ev.transition = Transition(kind, dur)
    el = EditList(events=[ev], rate=R25)
    text = write_edl(el)
    back = parse_edl(text, R25)
    assert back.events[0].transition.kind == kind
    assert back.events[0].transition.duration == dur


def test_roundtrip_drop_frame():
    from conformkit.model import EditList
    from conformkit.timecode import add
    base = Timecode(0, 0, 0, 0)
    ev = Event(
        rec_in=add(base, 1800, DF2997), rec_out=add(base, 1900, DF2997),
        src_in=base, src_out=add(base, 100, DF2997),
        roll_name="R1", clip_name="C", tracks=parse_track_text("V"),
        transition=Transition("C", 0), number=1,
    )
    el = EditList(events=[ev], rate=DF2997, title="DF")
    text = write_edl(el)
    assert "FCM: DROP FRAME" in text
    assert ";" in text
    back = parse_edl(text, DF2997)
    assert back.events[0].rec_in == ev.rec_in


def test_roundtrip_max_roll_lengths():
    from conformkit.model import EditList
    for dialect, limit in [("File16", 16), ("File32", 32), ("File129", 129)]:
        roll = "R" * limit
        el = EditList(events=[make_event(1, roll=roll)], rate=R25)
        text = write_edl(el, dialect)
        back = parse_edl(text, R25, dialect)
        assert back.events[0].roll_name == roll


def test_roundtrip_1000_events():
    from conformkit.model import EditList
    events = [make_event(i + 1, start=i * 200, dur=100) for i in range(1000)]
    el = EditList(events=events, rate=R25, title="BIG")
    text = write_edl(el)
    back = parse_edl(text, R25)
    assert len(back) == 1000
    for a, b in zip(el, back):
        assert a.rec_in == b.rec_in and a.roll_name == b.roll_name


# --------------------------------------------------------------- negatives

def _base_text(**over):
    from conformkit.model import EditList
    el = EditList(events=[make_event(1)], rate=R25)
    return write_edl(el)


def test_rejects_truncated_event():
    text = "TITLE: X\nFCM: NON-DROP FRAME\n001 R1 V C 00:00:00:00 00:00:04:00\n"
    with pytest.raises(EDLError):
        parse_edl(text, R25)


def test_rejects_bad_timecode():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V C 00:00:00:99 00:00:04:00 00:00:00:00 00:00:04:00\n")
    with pytest.raises(EDLError) as ei:
        parse_edl(text, R25)
    assert "99" in str(ei.value)


def test_rejects_roll_too_long():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 ROLLNAME_THAT_IS_TOO_LONG_FOR_FILE16 V C "
            "00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n")
    with pytest.raises(EDLError) as ei:
        parse_edl(text, R25, "File16")
    assert "16" in str(ei.value)


def test_rejects_duration_mismatch():
    """A source/record duration mismatch is a warning, not a rejection:
    real lists use it for speed changes and freeze frames."""
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V C 00:00:00:00 00:00:10:00 00:00:00:00 00:00:04:00\n")
    el = parse_edl(text, R25)
    assert len(el) == 1
    assert any("source duration" in w for w in el.warnings)


def test_rejects_non_monotonic_numbers():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "002 R1 V C 00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n"
            "001 R2 V C 00:00:04:00 00:00:08:00 00:00:04:00 00:00:08:00\n")
    with pytest.raises(EDLError) as ei:
        parse_edl(text, R25)
    assert "monotonic" in str(ei.value)


def test_rejects_fcm_rate_mismatch():
    text = ("TITLE: X\nFCM: DROP FRAME\n"
            "001 R1 V C 00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n")
    with pytest.raises(EDLError) as ei:
        parse_edl(text, R25)
    assert "drop" in str(ei.value).lower()


def test_zero_length_event_is_warned_not_rejected():
    """Transition anchors are written as zero-length events in real lists."""
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V C 00:00:00:00 00:00:00:00 00:00:00:00 00:00:00:00\n")
    el = parse_edl(text, R25)
    assert len(el) == 1
    assert any("zero record duration" in w for w in el.warnings)


def test_rejects_cut_with_duration():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V C 10 00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n")
    with pytest.raises(EDLError):
        parse_edl(text, R25)


def test_rejects_transition_without_duration():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V D 00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n")
    with pytest.raises(EDLError):
        parse_edl(text, R25)


def test_no_partial_list_on_error():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V C 00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n"
            "002 R2 V C 00:00:04:00 00:00:08:00 00:00:04:00 00:00:99:00\n")
    with pytest.raises(EDLError):
        parse_edl(text, R25)


# --------------------------------------------------------------- helpers

def test_detect_dialect():
    from conformkit.model import EditList
    short = write_edl(EditList(events=[make_event(1, roll="R1")], rate=R25))
    assert detect_dialect(short) == "CMX3600"
    long = write_edl(EditList(events=[make_event(1, roll="R" * 30)], rate=R25),
                     "File32")
    assert detect_dialect(long) == "File32"


def test_frame_rate_hint():
    assert frame_rate_hint("FCM: DROP FRAME\n") is True
    assert frame_rate_hint("FCM: NON-DROP FRAME\n") is False
    assert frame_rate_hint("TITLE: X\n") is None


def test_comment_parsing():
    text = ("TITLE: X\nFCM: NON-DROP FRAME\n"
            "001 R1 V C 00:00:00:00 00:00:04:00 00:00:00:00 00:00:04:00\n"
            "* FROM CLIP NAME: MYCLIP\n"
            "* SOURCE FILE: /a/b.wav\n"
            "* a free comment\n")
    el = parse_edl(text, R25)
    ev = el.events[0]
    assert ev.clip_name == "MYCLIP"
    assert ev.source_file == "/a/b.wav"
    assert "a free comment" in ev.comments
