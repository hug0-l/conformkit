"""Cross-validation against pycmx, an independent CMX3600 implementation.

pycmx (github.com/iluvcapra/pycmx, MIT) is the reference implementation that
owns the fixtures used in test_real_edl.py. Agreement between two independently
written parsers on the same real-world input is external evidence that the
reader is not merely self-consistent.

pycmx models a file as events, each holding one or more edits (a dissolve is
two edits under one event). That is the same shape this implementation uses,
so the comparison flattens both to a per-edit sequence.

Note on drop-frame: the fixtures here are non-drop at 25 or 24 fps, so frame
counts are compared at a nominal 30 fps basis only to keep the arithmetic
identical on both sides. Rate correctness is covered by test_timecode.py.
"""
import glob
import os

import pytest

pycmx = pytest.importorskip("pycmx")

from conformkit.edl import parse_edl                    # noqa: E402
from conformkit.timecode import rate_for                # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def fixture_paths():
    out = []
    for p in sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.edl"))):
        if os.path.getsize(p) > 50:
            out.append(p)
    return out


PATHS = fixture_paths()
pytestmark = pytest.mark.skipif(not PATHS, reason="no fixtures")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def reference_edits(path):
    """Flatten pycmx events to a list of edits with normalised fields."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        edit = pycmx.parse_cmx3600(fh, tolerant=True)

    out = []
    for event in edit.events:
        for e in event.edits:
            out.append({
                "source": str(e.source),
                "src_in": str(e.source_in),
                "src_out": str(e.source_out),
                "rec_in": str(e.record_in),
                "rec_out": str(e.record_out),
                "clip_name": str(e.clip_name or ""),
            })
    return out


def our_edits(path):
    el = parse_edl(read(path), rate_for("25"), "CMX3600",
                   strict_roll_length=False)
    return [{
        "source": ev.roll_name,
        "src_in": str(ev.src_in),
        "src_out": str(ev.src_out),
        "rec_in": str(ev.rec_in),
        "rec_out": str(ev.rec_out),
        "clip_name": ev.clip_name,
    } for ev in el]


def _frames(label: str, nominal: int = 30) -> int:
    parts = label.replace(";", ":").split(":")
    if len(parts) != 4:
        return -1
    h, m, s, f = (int(x) for x in parts)
    return ((h * 60 + m) * 60 + s) * nominal + f


# ------------------------------------------------------------------ counts

@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_edit_count_matches_reference(path):
    ref = reference_edits(path)
    ours = our_edits(path)
    assert len(ours) == len(ref), (
        f"{os.path.basename(path)}: we parsed {len(ours)} edits, "
        f"pycmx parsed {len(ref)}")


# ------------------------------------------------------------------ source

@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_source_identifiers_match_reference(path):
    ref = [e["source"] for e in reference_edits(path)]
    ours = [e["source"] for e in our_edits(path)]
    assert ours == ref, f"{os.path.basename(path)}: source sequence differs"


# ------------------------------------------------------------------ timecodes

@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_timecodes_match_reference(path):
    ref = reference_edits(path)
    ours = our_edits(path)

    mismatches = []
    for i, (r, o) in enumerate(zip(ref, ours), start=1):
        for field in ("src_in", "src_out", "rec_in", "rec_out"):
            if _frames(r[field]) != _frames(o[field]):
                mismatches.append(
                    f"edit {i} {field}: ref={r[field]} ours={o[field]}")

    assert not mismatches, (
        f"{os.path.basename(path)}: {len(mismatches)} timecode mismatches\n"
        + "\n".join(mismatches[:10]))


# ------------------------------------------------------------------ clip names

@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_clip_names_match_reference(path):
    """Clip names come from '* FROM CLIP NAME:' comments in both parsers."""
    ref = [e["clip_name"] for e in reference_edits(path)]
    ours = [e["clip_name"] for e in our_edits(path)]

    mismatches = [(i, r, o) for i, (r, o) in enumerate(zip(ref, ours), start=1)
                  if r != o]
    assert not mismatches, (
        f"{os.path.basename(path)}: {len(mismatches)} clip-name mismatches\n"
        + "\n".join(f"  edit {i}: ref={r!r} ours={o!r}"
                    for i, r, o in mismatches[:10]))


# ------------------------------------------------------------------ record continuity

@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_record_ranges_agree_between_parsers(path):
    """For every edit, rec_out - rec_in must be identical in both parsers."""
    ref = reference_edits(path)
    ours = our_edits(path)

    diffs = []
    for i, (r, o) in enumerate(zip(ref, ours), start=1):
        r_dur = _frames(r["rec_out"]) - _frames(r["rec_in"])
        o_dur = _frames(o["rec_out"]) - _frames(o["rec_in"])
        if r_dur != o_dur:
            diffs.append(f"edit {i}: ref dur {r_dur} ours dur {o_dur}")

    assert not diffs, (
        f"{os.path.basename(path)}: {len(diffs)} duration differences\n"
        + "\n".join(diffs[:10]))
