"""Audio-file and region decoding tests.

The decisive test is the ground-truth one: the Low Roar demo ships the session
*and* its audio, so every region's `sample_offset + length` can be checked
against the real WAV length. With little-endian decoding that must hold for
every region; with big-endian it holds for roughly a quarter of them.
"""
import os
import struct

import pytest

from conformkit.ptblocks import parse_blocks
from conformkit.ptdecrypt import decrypt
from conformkit.ptregions import (
    AUDIO_FILE_LIST_TYPE, AUDIO_INFO_TYPE, AUDIO_META_TYPE, AUDIO_NAME_TYPE,
    REGION_LIST_TYPES, REGION_TYPES, AudioFile, Region, RegionError,
    extract_audio_files, extract_playlists, extract_regions,
    samples_to_timecode,
)
from conformkit.timecode import rate_for

PROD = os.path.join(os.path.dirname(__file__), "..", "fixtures", "production")
PROD_PTX = os.path.join(PROD, "GP04_DSS1.ptx")
DEMO_DIR = os.path.join(os.path.dirname(__file__), "..", "work", "lowroar")
DEMO_PTX = os.path.join(DEMO_DIR, "Low Roar Pro Tools Demo.ptx")
DEMO_AUDIO = os.path.join(DEMO_DIR, "audio")

needs_prod = pytest.mark.skipif(not os.path.exists(PROD_PTX),
                                reason="production session not present")
needs_demo = pytest.mark.skipif(not os.path.exists(DEMO_PTX),
                                reason="demo session not present")
R25 = rate_for("25")


def parse(path):
    plain, _ = decrypt(open(path, "rb").read())
    return plain, parse_blocks(plain, big_endian=bool(plain[0x11])), bool(plain[0x11])


@pytest.fixture(scope="module")
def demo():
    plain, blocks, be = parse(DEMO_PTX)
    files = extract_audio_files(plain, blocks, be)
    return plain, blocks, be, files, extract_regions(plain, blocks, be, files)


@pytest.fixture(scope="module")
def prod():
    plain, blocks, be = parse(PROD_PTX)
    files = extract_audio_files(plain, blocks, be)
    return plain, blocks, be, files, extract_regions(plain, blocks, be, files)


def wav_frames(path):
    """Frame count from a RIFF/WAVE header.

    WAV `fmt ` layout after the 8-byte chunk header:
      +0 format, +2 channels, +4 sample rate, +8 byte rate,
      +12 block align, +14 bits per sample
    Block align is at chunk_offset+20, bits at +22 — reading +22 as block
    align divides by the bit depth and under-reports frames by 8x for 32-bit.
    """
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if head[:4] != b"RIFF":
        return None
    off, align, size = 12, 0, None
    while off + 8 <= len(head):
        cid = head[off:off + 4]
        n = struct.unpack_from("<I", head, off + 4)[0]
        if cid == b"fmt ":
            align = struct.unpack_from("<H", head, off + 20)[0]
        elif cid == b"data":
            size = n
            break
        off += 8 + n + (n & 1)
    return size // align if align and size is not None else None


# --------------------------------------------------------------- constants

def test_content_types():
    assert AUDIO_FILE_LIST_TYPE == 0x1004
    assert AUDIO_NAME_TYPE == 0x103A
    assert AUDIO_META_TYPE == 0x1003
    assert AUDIO_INFO_TYPE == 0x1001
    assert 0x262A in REGION_LIST_TYPES and 0x2629 in REGION_TYPES


# --------------------------------------------------------------- primitives

def test_samples_to_timecode():
    assert samples_to_timecode(0, 48000, R25).hh == 0
    tc = samples_to_timecode(48000, 48000, R25)
    assert (tc.ss, tc.ff) == (1, 0)


def test_samples_to_timecode_rejects_zero_rate():
    with pytest.raises(RegionError):
        samples_to_timecode(1, 0, R25)


def test_audio_file_duration():
    f = AudioFile(index=0, name="x.wav", sample_rate=48000, length=48000)
    assert f.duration_seconds == pytest.approx(1.0)
    assert AudioFile(index=0).duration_seconds == 0.0


def test_region_end_is_offset_plus_length():
    r = Region(name="x", block_offset=0, sample_offset=100, length=50)
    assert r.end == 150


def test_decode_triple_rejects_bad_widths():
    from conformkit.ptregions import _decode_triple

    data = bytearray(64)
    data[1] = 0xF0
    assert _decode_triple(bytes(data), 0) is None
    assert _decode_triple(bytes(64), 0) is None


# --------------------------------------------------------------- demo file

@needs_demo
def test_demo_audio_files(demo):
    _, _, _, files, _ = demo
    assert len(files) > 50
    assert all(f.sample_rate == 48000 for f in files if f.length)
    assert all(f.length > 0 for f in files)
    assert all(f.name.endswith((".wav", ".L", ".R")) or f.name for f in files)


@needs_demo
def test_demo_audio_lengths_match_the_real_wavs(demo):
    """The length in the session must equal the length in the file."""
    _, _, _, files, _ = demo
    checked = 0
    for f in files:
        path = os.path.join(DEMO_AUDIO, f.name)
        if not os.path.exists(path):
            continue
        actual = wav_frames(path)
        if actual is None:
            continue
        assert f.length == actual, f"{f.name}: session {f.length} vs file {actual}"
        checked += 1
    assert checked >= 10, "expected to compare at least ten real WAVs"


@needs_demo
def test_demo_regions_all_fit_their_source(demo):
    """Ground truth: every region lies inside its source recording."""
    _, _, _, files, regions = demo
    assert regions
    bounded = [r for r in regions
               if 0 <= r.source_index < len(files) and files[r.source_index].length]
    assert bounded
    bad = [r for r in bounded if not r.reliable]
    assert not bad, f"{len(bad)} regions exceed their source length"


@needs_demo
def test_demo_region_names_are_distinct(demo):
    _, _, _, _, regions = demo
    names = {r.name for r in regions}
    assert len(names) > len(regions) // 2


# --------------------------------------------------------------- prod file

@needs_prod
def test_prod_audio_files(prod):
    _, _, _, files, _ = prod
    assert len(files) > 1000
    lengths = [f.length for f in files if f.length]
    assert min(lengths) > 0
    assert max(lengths) < 48_000 * 60 * 60 * 8


@needs_prod
def test_prod_regions_bound(prod):
    _, _, _, files, regions = prod
    assert len(regions) > 1000
    bounded = [r for r in regions
               if 0 <= r.source_index < len(files) and files[r.source_index].length]
    assert len(bounded) > len(regions) * 0.95
    ok = sum(1 for r in bounded if r.reliable)
    assert ok / len(bounded) > 0.99, f"{ok}/{len(bounded)} fit"


@needs_prod
def test_endianness_matters(prod):
    """Reading the triple big-endian must fail the bound wholesale.

    This pins the endianness decision to evidence rather than convention.
    """
    from conformkit.ptregions import _decode_triple

    plain, blocks, be, files, regions = prod
    # re-decode region triples as big-endian and show the bound breaks
    checked = 0
    broken = 0
    from conformkit.ptblocks import walk

    for _, parent in walk(blocks):
        if parent.content_type not in REGION_LIST_TYPES:
            continue
        for record in parent.children:
            if record.content_type not in REGION_TYPES:
                continue
            n = struct.unpack_from("<I", plain, record.payload_offset + 11)[0]
            if not (1 <= n <= 512):
                continue
            base = record.payload_offset + 15 + n
            widths = [(plain[base + i] & 0xF0) >> 4 for i in (1, 2, 3)]
            if any(w > 5 for w in widths) or sum(widths) == 0:
                continue
            pos = base + 5
            vals = []
            for w in widths:
                vals.append(int.from_bytes(plain[pos:pos + w], "big") if w else 0)
                pos += w
            child = record.children[0] if record.children else None
            if not child:
                continue
            src = struct.unpack_from("<I", plain,
                                     child.offset + 7 + child.block_size)[0]
            if not (0 <= src < len(files)) or not files[src].length:
                continue
            checked += 1
            if vals[0] + vals[1] > files[src].length:
                broken += 1
            if checked >= 200:
                break
        if checked >= 200:
            break

    assert checked >= 50
    assert broken / checked > 0.5, "big-endian should break most bounds"


@needs_prod
def test_extraction_is_deterministic(prod):
    plain, blocks, be, files, regions = prod
    again = extract_regions(plain, blocks, be, files)
    assert [(r.name, r.sample_offset, r.length) for r in regions] == \
           [(r.name, r.sample_offset, r.length) for r in again]


@needs_prod
def test_playlists_do_not_crash(prod):
    plain, blocks, be, _, _ = prod
    assert isinstance(extract_playlists(plain, blocks, be), list)
