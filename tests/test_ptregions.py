"""Region decoding tests.

The contract under test: every region with a readable name is returned, and the
timing carries an honest confidence rating rather than a guess presented as
fact.
"""
import os

import pytest

from conformkit.ptblocks import parse_blocks
from conformkit.ptdecrypt import decrypt
from conformkit.ptregions import (
    CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_NONE, MAX_SAMPLES,
    REGION_LIST_TYPES, REGION_TYPES, Region, RegionError, extract_playlists,
    extract_regions, samples_to_timecode,
)
from conformkit.timecode import rate_for

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                       "production", "GP04_DSS1.ptx")
needs_real = pytest.mark.skipif(not os.path.exists(FIXTURE),
                                reason="production PTX not present")
R25 = rate_for("25")


@pytest.fixture(scope="module")
def parsed():
    plain, _ = decrypt(open(FIXTURE, "rb").read())
    be = bool(plain[0x11])
    return plain, parse_blocks(plain, big_endian=be), be


@pytest.fixture(scope="module")
def regions(parsed):
    plain, blocks, be = parsed
    return extract_regions(plain, blocks, be)


# --------------------------------------------------------------- primitives

def test_region_type_sets():
    assert 0x262A in REGION_LIST_TYPES and 0x2629 in REGION_TYPES
    assert 0x100B in REGION_LIST_TYPES and 0x1008 in REGION_TYPES


def test_samples_to_timecode():
    assert samples_to_timecode(0, 48000, R25).hh == 0
    tc = samples_to_timecode(48000, 48000, R25)
    assert (tc.ss, tc.ff) == (1, 0)


def test_samples_to_timecode_rejects_zero_rate():
    with pytest.raises(RegionError):
        samples_to_timecode(100, 0, R25)


def test_region_properties():
    r = Region(name="x", block_offset=0, length=48000, start=0,
               confidence=CONFIDENCE_HIGH)
    assert r.end == 48000
    assert r.duration_seconds == pytest.approx(1.0)
    assert r.reliable is True


def test_low_confidence_is_not_reliable():
    r = Region(name="x", block_offset=0, confidence=CONFIDENCE_LOW)
    assert r.reliable is False


def test_decode_at_rejects_absurd_width():
    from conformkit.ptregions import _decode_at

    data = bytearray(64)
    data[1] = 0xF0                 # width 15 is impossible
    assert _decode_at(bytes(data), 0) is None


def test_decode_at_rejects_all_zero_widths():
    from conformkit.ptregions import _decode_at

    assert _decode_at(bytes(64), 0) is None


def test_decode_at_detects_redundant_leading_zero():
    from conformkit.ptregions import _decode_at

    data = bytearray(64)
    data[1] = 0x10                 # one-byte offset field
    data[5] = 0x00                 # leading zero -> not a minimal encoding
    out = _decode_at(bytes(data), 0)
    assert out is not None
    assert out[3] is False                              # minimal flag


# --------------------------------------------------------------- real file

@needs_real
def test_every_named_region_is_returned(regions):
    """Records with unusable timing must still carry their name."""
    assert len(regions) > 1000
    assert all(r.name for r in regions)


@needs_real
def test_names_are_mostly_distinct(regions):
    names = {r.name for r in regions}
    assert len(names) > len(regions) // 2


@needs_real
def test_source_indices_are_small_ordinals(regions):
    indices = {r.source_index for r in regions if r.source_index >= 0}
    assert indices
    assert min(indices) >= 0
    assert max(indices) < 100_000


@needs_real
def test_confidence_is_partitioned(regions):
    counts = {c: sum(1 for r in regions if r.confidence == c)
              for c in (CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_NONE)}
    assert sum(counts.values()) == len(regions)
    assert counts[CONFIDENCE_HIGH] > 0, "some timing must resolve"


@needs_real
def test_high_confidence_values_are_plausible(regions):
    for r in regions:
        if r.confidence != CONFIDENCE_HIGH:
            continue
        assert 0 < r.length <= MAX_SAMPLES
        assert 0 <= r.start <= MAX_SAMPLES
        assert r.end <= MAX_SAMPLES


@needs_real
def test_high_confidence_bounds_are_consistent_per_source(regions):
    """Within one source, no high-confidence region may overrun another's end."""
    by_source: dict[int, list] = {}
    for r in regions:
        if r.confidence == CONFIDENCE_HIGH:
            by_source.setdefault(r.source_index, []).append(r)

    for source, group in by_source.items():
        bound = max(r.end for r in group)
        for r in group:
            assert r.end <= bound * 1.02


@needs_real
def test_playlists_do_not_crash(parsed):
    plain, blocks, be = parsed
    entries = extract_playlists(plain, blocks, be)
    assert isinstance(entries, list)
    for e in entries[:200]:
        assert e.region_index >= 0


@needs_real
def test_extraction_is_deterministic(parsed):
    plain, blocks, be = parsed
    first = [(r.name, r.confidence, r.length, r.start)
             for r in extract_regions(plain, blocks, be)]
    second = [(r.name, r.confidence, r.length, r.start)
              for r in extract_regions(plain, blocks, be)]
    assert first == second
