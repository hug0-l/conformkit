"""Validation against real-world EDLs.

The fixtures are production edit lists from the pycmx test corpus
(github.com/iluvcapra/pycmx, MIT licensed), covering the dialects that actually
occur in the wild: standard CMX3600, CDL/FRMC annotated lists, dissolves and
wipes written as two lines, unusual generator output, and conform issue cases.

These tests assert behaviour on real input, not on synthetic data.
"""
import glob
import os

import pytest

from conformkit.edl import EDLError, detect_dialect, parse_edl, write_edl
from conformkit.timecode import rate_for, to_frames

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
R25 = rate_for("25")


def fixture_paths():
    pattern = os.path.join(FIXTURE_DIR, "*.edl")
    out = []
    for p in sorted(glob.glob(pattern)):
        if os.path.getsize(p) > 50:      # skip the 404 stubs
            out.append(p)
    return out


PATHS = fixture_paths()


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


pytestmark = pytest.mark.skipif(not PATHS, reason="no fixtures downloaded")


# --------------------------------------------------------------- parse

@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_real_edl_parses(path):
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    assert len(el) > 0, "a real list must contain events"
    assert el.title, "a real list must carry a title"


@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_real_edl_events_are_coherent(path):
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    for ev in el:
        assert ev.rec_in is not None
        assert ev.rec_out is not None
        assert ev.roll_name
        assert ev.tracks, f"event {ev.number} has no track"
        # record duration must be non-negative; zero-length is legal in the
        # wild (a transition anchor) but never negative
        assert ev.rec_duration(R25) >= 0


@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_real_edl_roundtrip_preserves_timecodes(path):
    original = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    text = write_edl(original, "CMX3600")
    back = parse_edl(text, R25, "CMX3600", strict_roll_length=False)

    assert len(back) == len(original), "event count changed on round-trip"

    for a, b in zip(original, back):
        assert a.rec_in == b.rec_in, f"rec_in changed at event {a.number}"
        assert a.rec_out == b.rec_out, f"rec_out changed at event {a.number}"
        assert a.src_in == b.src_in
        assert a.src_out == b.src_out
        assert a.roll_name == b.roll_name
        assert set(a.tracks.tracks) == set(b.tracks.tracks)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_real_edl_roundtrip_is_stable(path):
    """Writing a parsed list twice must be byte-identical."""
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    first = write_edl(el, "CMX3600")
    el2 = parse_edl(first, R25, "CMX3600", strict_roll_length=False)
    second = write_edl(el2, "CMX3600")
    assert first == second


# --------------------------------------------------------------- specifics

def test_dissolve_two_line_convention():
    """test_25.edl contains a dissolve written as two lines sharing event 005."""
    path = os.path.join(FIXTURE_DIR, "test_25.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    dissolves = [e for e in el if e.transition.kind == "D"]
    assert dissolves, "the dissolve must be recognised"
    assert dissolves[0].transition.duration == 70


def test_wipe_with_pattern_and_duration():
    """test_25.edl carries 'W001 025': wipe pattern 001, duration 25."""
    path = os.path.join(FIXTURE_DIR, "test_25.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    wipes = [e for e in el if e.transition.kind == "W"]
    assert wipes, "the wipe must be recognised"
    assert wipes[0].transition.wipe_code == "001"
    assert wipes[0].transition.duration == 25


def test_effects_name_captured():
    path = os.path.join(FIXTURE_DIR, "test_25.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    named = [e for e in el if e.effects_name]
    assert any("Constant Power" in e.effects_name for e in named)


def test_comment_without_space_is_recognised():
    """cdl_frmc_example01 uses '*FROM CLIP NAME:' with no space."""
    path = os.path.join(FIXTURE_DIR, "cdl_frmc_example01.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    assert el.events[0].clip_name.startswith("QTLF_101")


def test_cdl_annotations_preserved():
    path = os.path.join(FIXTURE_DIR, "cdl_frmc_example01.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    joined = " ".join(el.events[0].comments)
    assert "ASC_SOP" in joined
    assert "FRMC" in joined


def test_motion_annotation_preserved():
    path = os.path.join(FIXTURE_DIR, "test_25.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    assert any(any(a.startswith("M2") for a in e.annotations) for e in el)


def test_rem_lines_preserved():
    path = os.path.join(FIXTURE_DIR, "test_25.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    assert any("MIT License" in r for r in el.remarks)


def test_audio_continuation_preserved():
    path = os.path.join(FIXTURE_DIR, "test_25.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    el = parse_edl(read(path), R25, "CMX3600", strict_roll_length=False)
    assert any(any(a.startswith("AUD") for a in e.annotations) for e in el)


def test_long_roll_names_need_permissive_dialect():
    """ISSUE_19 has 22-char roll names: valid for File32, too long for File16."""
    path = os.path.join(FIXTURE_DIR, "ISSUE_19_unusual01.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    text = read(path)
    assert detect_dialect(text) in ("File32", "File129")
    with pytest.raises(EDLError):
        parse_edl(text, R25, "File16")
    el = parse_edl(text, R25, "File32")
    assert len(el) == 38


def test_large_list_performance():
    """TEST.edl has 148 edits (120 numbered, 28 as transition-pair seconds)
    and 434 comments; must parse fast."""
    import time
    path = os.path.join(FIXTURE_DIR, "TEST.edl")
    if not os.path.exists(path):
        pytest.skip("fixture missing")
    text = read(path)
    start = time.perf_counter()
    for _ in range(20):
        el = parse_edl(text, R25, "CMX3600", strict_roll_length=False)
    elapsed = time.perf_counter() - start
    assert len(el) == 148
    assert elapsed < 5.0, f"20 parses took {elapsed:.2f}s"
