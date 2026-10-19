"""Compare and match tests."""
import pytest

from conformkit.compare import (
    ALL_OFF_SETTINGS, ChangeType, CompareSettings, compare_lists,
)
from conformkit.match import MatchKind, MatchSettings, match_events
from conformkit.model import EditList, Event, Transition, parse_track_text
from conformkit.timecode import Timecode, add, rate_for

R25 = rate_for("25")
BASE = Timecode(0, 0, 0, 0)


def ev(roll="R1", clip="C1", start=0, dur=100, track="V", rate=R25):
    return Event(
        rec_in=add(BASE, start, rate), rec_out=add(BASE, start + dur, rate),
        src_in=add(BASE, start + 1000, rate),
        src_out=add(BASE, start + dur + 1000, rate),
        roll_name=roll, clip_name=clip,
        tracks=parse_track_text(track), transition=Transition("C", 0),
    )


def el(events, rate=R25, title="L"):
    return EditList(events=events, rate=rate, title=title)


# --------------------------------------------------------------- compare

def test_identical_lists_have_no_changes():
    a = el([ev(start=i * 200) for i in range(5)])
    b = el([ev(start=i * 200) for i in range(5)])
    cl = compare_lists(a, b)
    assert len(cl) == 0


def test_detects_added_event():
    a = el([ev(start=0)])
    b = el([ev(start=0), ev(roll="R2", start=200)])
    cl = compare_lists(a, b)
    assert cl.counts() == {"added": 1}


def test_detects_removed_event():
    a = el([ev(start=0), ev(roll="R2", start=200)])
    b = el([ev(start=0)])
    cl = compare_lists(a, b)
    assert cl.counts() == {"removed": 1}


def test_detects_moved_event():
    a = el([ev(start=0)])
    b = el([ev(start=25)])
    cl = compare_lists(a, b)
    assert cl.counts() == {"moved": 1}
    assert cl.changes[0].delta == 25


def test_detects_trimmed_event():
    a = el([ev(start=0, dur=100)])
    b = el([ev(start=0, dur=125)])
    cl = compare_lists(a, b)
    assert cl.counts() == {"trimmed": 1}
    assert cl.changes[0].delta == 25


def test_move_and_trim_reported_separately():
    a = el([ev(start=0, dur=100)])
    b = el([ev(start=10, dur=120)])
    cl = compare_lists(a, b)
    assert cl.counts() == {"moved": 1, "trimmed": 1}


def test_tolerance_suppresses_small_trims():
    s = CompareSettings(tolerance_frames=2)
    a = el([ev(start=0, dur=100)])
    b = el([ev(start=0, dur=102)])
    assert len(compare_lists(a, b, s)) == 0


def test_report_unchanged():
    s = CompareSettings(report_unchanged=True)
    a = el([ev(start=0)])
    b = el([ev(start=0)])
    cl = compare_lists(a, b, s)
    assert cl.counts() == {"unchanged": 1}


def test_deletions_can_be_suppressed():
    s = CompareSettings(report_deletions=False)
    a = el([ev(start=0), ev(roll="R2", start=200)])
    b = el([ev(start=0)])
    assert len(compare_lists(a, b, s)) == 0


def test_all_off_settings_match_nothing():
    a = el([ev(start=0)])
    b = el([ev(start=0)])
    cl = compare_lists(a, b, ALL_OFF_SETTINGS)
    # no match axes -> every old removed, every new added
    assert cl.counts() == {"added": 1}


def test_deterministic():
    a = el([ev(start=i * 200) for i in range(20)])
    b = el([ev(start=i * 200 + (5 if i % 3 else 0)) for i in range(20)])
    first = [(c.change_type, c.delta) for c in compare_lists(a, b).changes]
    for _ in range(5):
        assert [(c.change_type, c.delta)
                for c in compare_lists(a, b).changes] == first


def test_shuffle_input_order_gives_same_change_set():
    import random
    evs_a = [ev(start=i * 200, roll=f"R{i}") for i in range(10)]
    evs_b = [ev(start=i * 200 + 3, roll=f"R{i}") for i in range(10)]
    a = el(list(evs_a))
    b = el(list(evs_b))
    baseline = sorted((c.change_type.value, c.delta) for c in compare_lists(a, b).changes)

    rng = random.Random(7)
    for _ in range(5):
        rng.shuffle(evs_a)
        rng.shuffle(evs_b)
        got = sorted((c.change_type.value, c.delta)
                     for c in compare_lists(el(evs_a), el(evs_b)).changes)
        assert got == baseline


def test_rejects_rate_mismatch():
    a = el([ev()], rate=R25)
    b = el([ev()], rate=rate_for("24"))
    with pytest.raises(ValueError, match="frame rate"):
        compare_lists(a, b)


def test_noisy_detection():
    a = el([ev(start=i * 200) for i in range(20)])
    b = el([ev(start=i * 200 + 1) for i in range(20)])
    cl = compare_lists(a, b)
    assert cl.is_noisy() is True


def test_single_track_detection():
    # a change list spanning different tracks is not single-track
    a = el([ev(track="V", start=0)])
    b = el([ev(track="A", start=0, roll="R2")])
    cl = compare_lists(a, b)
    assert len(cl) > 0
    assert cl.single_track() is False

    # an all-on-one-track list is
    a2 = el([ev(track="V", start=0)])
    b2 = el([ev(track="V", start=10)])
    assert compare_lists(a2, b2).single_track() is True


# --------------------------------------------------------------- match

class FakeWav:
    def __init__(self, path="", tape=""):
        from conformkit.bwf import WavInfo
        self._w = WavInfo(path=path)
        self._w.ix_tape = tape

    def __getattr__(self, name):
        return getattr(self._w, name)


def wav(path="", tape=""):
    from conformkit.bwf import WavInfo
    w = WavInfo(path=path)
    w.ix_tape = tape
    return w


def test_match_by_roll():
    events = [ev(roll="R0042")]
    wavs = [wav(path="/a/b.wav", tape="R0042")]
    r = match_events(events, wavs)
    assert r.records[0].kind == MatchKind.ROLL
    assert r.stats()["matched"] == 1


def test_match_by_filename():
    events = [ev(roll="NOPE", clip="scene1.wav")]
    wavs = [wav(path="/x/scene1.wav")]
    r = match_events(events, wavs, MatchSettings(by_roll=False, by_filename=True))
    assert r.records[0].kind == MatchKind.FILENAME


def test_match_both_agree():
    events = [ev(roll="R1", clip="R1.wav")]
    wavs = [wav(path="/x/R1.wav", tape="R1")]
    r = match_events(events, wavs)
    assert r.records[0].kind == MatchKind.BOTH
    assert r.records[0].diff_roll_names is False


def test_roll_precedence_when_disagreeing():
    # two different WAVs: one matches by roll, the other by filename
    events = [ev(roll="R1", clip="other.wav")]
    wavs = [wav(path="/x/ROLLFILE.wav", tape="R1"),
            wav(path="/x/other.wav", tape="DIFFERENT")]
    r = match_events(events, wavs)
    assert r.records[0].kind == MatchKind.ROLL
    assert r.records[0].diff_roll_names is True


def test_both_axes_agreeing_on_same_file():
    events = [ev(roll="R1", clip="R1.wav")]
    wavs = [wav(path="/x/R1.wav", tape="R1")]
    r = match_events(events, wavs)
    assert r.records[0].kind == MatchKind.BOTH
    assert r.records[0].diff_roll_names is False


def test_unmatched_retained_not_dropped():
    events = [ev(roll="R1"), ev(roll="MISSING")]
    wavs = [wav(tape="R1")]
    r = match_events(events, wavs)
    assert r.stats() == {"total": 2, "matched": 1, "unmatched": 1}
    assert len(r.records) == 2


def test_case_insensitive_by_default():
    events = [ev(roll="r0042")]
    wavs = [wav(tape="R0042")]
    r = match_events(events, wavs)
    assert r.records[0].kind == MatchKind.ROLL


def test_case_sensitive_mode():
    events = [ev(roll="r0042")]
    wavs = [wav(tape="R0042")]
    r = match_events(events, wavs, MatchSettings(case_sensitive=True))
    assert r.records[0].kind == MatchKind.NONE
