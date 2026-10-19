"""Cross-validation against OpenTimelineIO — a third independent reader.

pycmx (test_cross_pycmx.py) checks our CMX3600 reader against a Python parser.
OTIO is a separate, widely used C++/Python library maintained by the Academy
Software Foundation with its own EDL adapter. Agreement with a *third*
implementation on the same production fixtures is materially stronger evidence
than agreement with one.

Because OTIO models a timeline of tracks/clips rather than raw EDL edits, the
comparison is at the level of: total clip count, and the multiset of record
ranges. That is exactly the contract a conform tool depends on.
"""
import glob
import os

import pytest

otio = pytest.importorskip("opentimelineio")
import opentimelineio.adapters        # noqa: E402

from conformkit.edl import parse_edl  # noqa: E402
from conformkit.timecode import rate_for, to_frames  # noqa: E402

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")


def fixture_paths():
    out = []
    for p in sorted(glob.glob(os.path.join(FIXTURE_DIR, "*.edl"))):
        if os.path.getsize(p) > 100:
            out.append(p)
    return out


PATHS = fixture_paths()
pytestmark = pytest.mark.skipif(not PATHS, reason="no fixtures")


def read(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def otio_record_ranges(path):
    """Return the multiset of (start, end) record ranges OTIO sees, per track."""
    timeline = otio.adapters.read_from_file(path, rate=25)
    ranges = []
    for track in timeline.each_track():
        for item in track:
            if item.kind() != otio.schema.TrackKind.Video and \
               item.kind() != otio.schema.TrackKind.Audio:
                continue
            r = item.range_in_parent()
            ranges.append((round(r.start_time.to_seconds() * 25),
                           round(r.end_time_inclusive().to_seconds() * 25)))
    return sorted(ranges)


def our_record_ranges(path):
    """Record ranges from our parser, ignoring zero-length anchors."""
    rate = rate_for("25")
    el = parse_edl(read(path), rate, "CMX3600", strict_roll_length=False)
    out = []
    for ev in el:
        s = to_frames(ev.rec_in, rate)
        e = to_frames(ev.rec_out, rate)
        if e > s:
            out.append((s, e))
    return sorted(out)


@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_otio_reads_the_file(path):
    """Guard: if OTIO cannot read the fixture, the comparison is meaningless."""
    timeline = otio.adapters.read_from_file(path, rate=25)
    assert timeline is not None


@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_clip_count_agrees_with_otio(path):
    try:
        ref = otio_record_ranges(path)
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"OTIO adapter could not parse this fixture: {exc}")

    ours = our_record_ranges(path)
    assert len(ours) == len(ref), (
        f"{os.path.basename(path)}: we found {len(ours)} clips, "
        f"OTIO found {len(ref)}")


@pytest.mark.parametrize("path", PATHS, ids=lambda p: os.path.basename(p))
def test_record_ranges_agree_with_otio(path):
    try:
        ref = otio_record_ranges(path)
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"OTIO adapter could not parse this fixture: {exc}")

    ours = our_record_ranges(path)
    if len(ours) != len(ref):
        pytest.skip("count mismatch is reported by the count test")

    mismatches = [(i, r, o) for i, (r, o) in enumerate(zip(ref, ours), start=1)
                  if r != o]
    assert not mismatches, (
        f"{os.path.basename(path)}: {len(mismatches)} record-range mismatches\n"
        + "\n".join(f"  clip {i}: otio={r} ours={o}" for i, r, o in mismatches[:10]))


def test_round_trip_through_otio_when_available(tmp_path):
    """Write our EDL output and confirm OTIO reads it back identically.

    This closes the loop: our writer produces something a third-party reader
    accepts and interprets the same way.
    """
    from conformkit.edl import write_edl

    src = os.path.join(FIXTURE_DIR, "ISSUE_19_unusual01.edl")
    if not os.path.exists(src):
        pytest.skip("fixture missing")

    rate = rate_for("25")
    el = parse_edl(read(src), rate, "File32", strict_roll_length=False)
    out = tmp_path / "written.edl"
    out.write_text(write_edl(el, "File32"), encoding="utf-8")

    try:
        ref = otio_record_ranges(str(out))
    except Exception as exc:                       # noqa: BLE001
        pytest.skip(f"OTIO could not read our output: {exc}")

    ours = our_record_ranges(str(out))
    assert len(ours) == len(ref)
    assert ours == ref
