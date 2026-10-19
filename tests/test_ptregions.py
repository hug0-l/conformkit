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
    PLAYLIST_MAP_TYPE, REGION_LIST_TYPES, REGION_TYPES, TIMELINE_UNIT_SAMPLES,
    TIMELINE_UNIT_TICKS, ZERO_TICKS, AudioFile, Region, RegionError,
    extract_audio_files, extract_playlists, extract_regions,
    samples_to_timecode,
)
from conformkit.timecode import rate_for

PROD = os.path.join(os.path.dirname(__file__), "..", "fixtures", "production")
PROD_PTX = os.path.join(PROD, "GP04_DSS1.ptx")
LEGACY_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "ptx")
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


# --------------------------------------------------------------- synthetic BE

def _block(ctype, payload, body_type=0, big_endian=True):
    """Build one block. `payload` is what follows the content-type field."""
    fmt = ">" if big_endian else "<"
    size = 2 + len(payload)
    return (b"\x5a" + struct.pack(fmt + "H", body_type)
            + struct.pack(fmt + "I", size) + struct.pack(fmt + "H", ctype)
            + payload)


def _pstr(text, big_endian=True):
    raw = text.encode("ascii")
    fmt = ">" if big_endian else "<"
    return struct.pack(fmt + "I", len(raw)) + raw


def _triple(values, big_endian=True, widths=(4, 4, 4)):
    """Pack a region triple: widths at the endian-specific bytes, LE values."""
    buf = bytearray(5 + sum(widths))
    if big_endian:
        buf[4] = widths[0] << 4
        buf[3] = widths[1] << 4
        buf[2] = widths[2] << 4
    else:
        buf[1] = widths[0] << 4
        buf[2] = widths[1] << 4
        buf[3] = widths[2] << 4
    pos = 5
    for value, width in zip(values, widths):
        buf[pos:pos + width] = value.to_bytes(width, "little")
        pos += width
    return bytes(buf)


def _be_region_tree(name="Kick-01", source=3, triple=(1000, 500, 2000)):
    """A big-endian 0x262A -> 0x2629 -> 0x2628 tree with one region."""
    name_blob = _pstr(name, True) + _triple(triple, True)
    name_block = _block(0x2628, name_blob, body_type=3, big_endian=True)
    record = _block(0x2629, name_block + struct.pack(">I", source),
                    body_type=7, big_endian=True)
    return _block(0x262A, record, body_type=1, big_endian=True)


def _be_audio_tree(files=(("A.wav", 48000, 2, 24, 123456),
                         ("B.wav", 96000, 1, 32, 654321))):
    """A big-endian 0x1004 list with 0x103A names and 0x1003/0x1001 records."""
    names = bytearray(b"\x00" * 5 + struct.pack(">I", len(files))
                      + struct.pack(">I", 0))
    for name, _, _, _, _ in files:
        names += b"\x00" * 9 + _pstr(name, True)
    name_block = _block(0x103A, bytes(names), body_type=1, big_endian=True)

    records = b""
    for _, rate, channels, bits, length in files:
        info = (struct.pack(">I", rate) + bytes([channels, bits])
                + struct.pack(">Q", length))
        records += _block(0x1003, _block(0x1001, info, big_endian=True),
                          body_type=1, big_endian=True)
    return _block(0x1004, name_block + records, body_type=2, big_endian=True)


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
    assert PLAYLIST_MAP_TYPE == 0x1054
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


# --------------------------------------------------------------- synthetic BE

def test_big_endian_region_tree_end_to_end():
    """A synthetic BE tree decodes through the real walker, not just the triple."""
    data = _be_region_tree()
    blocks = parse_blocks(data, start=0, big_endian=True)
    regions = extract_regions(data, blocks, big_endian=True)

    assert len(regions) == 1
    r = regions[0]
    assert (r.name, r.source_index) == ("Kick-01", 3)
    assert (r.sample_offset, r.length, r.start) == (1000, 500, 2000)
    assert r.end == 1500
    assert r.reliable is None          # no source file supplied


def test_big_endian_region_bound_is_checked():
    data = _be_region_tree(triple=(1000, 500, 2000))
    blocks = parse_blocks(data, start=0, big_endian=True)
    files = [AudioFile(index=i) for i in range(3)] + [
        AudioFile(index=3, name="src.wav", sample_rate=48000, length=1400)]
    regions = extract_regions(data, blocks, big_endian=True, files=files)
    assert regions[0].reliable is False       # 1500 > 1400

    files[3].length = 1500
    regions = extract_regions(data, blocks, big_endian=True, files=files)
    assert regions[0].reliable is True


def test_big_endian_audio_files_end_to_end():
    data = _be_audio_tree()
    blocks = parse_blocks(data, start=0, big_endian=True)
    files = extract_audio_files(data, blocks, big_endian=True)

    assert [f.name for f in files] == ["A.wav", "B.wav"]
    assert [f.sample_rate for f in files] == [48000, 96000]
    assert [(f.channels, f.bits) for f in files] == [(2, 24), (1, 32)]
    assert [f.length for f in files] == [123456, 654321]


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


# --------------------------------------------------------------- timeline

@needs_demo
def test_demo_placements_are_decoded(demo):
    plain, blocks, be, _, regions = demo
    placements = extract_playlists(plain, blocks, be, regions)

    assert len(placements) == 286
    assert all(0 <= p.region_index < len(regions) for p in placements)
    assert all(p.region_name for p in placements), "every id resolves"

    sample = [p for p in placements if p.unit == TIMELINE_UNIT_SAMPLES]
    tick = [p for p in placements if p.unit == TIMELINE_UNIT_TICKS]
    assert (len(sample), len(tick)) == (177, 109)

    # A sample position must lie inside the session's audio extent.
    extent = max(r.end for r in regions)
    assert all(p.timeline_start is not None and p.timeline_start < extent
               for p in sample)
    # Tick positions are reported raw: no sample conversion is claimed.
    assert all(p.timeline_start is None for p in tick)
    assert all(p.timeline_ticks == p.position - ZERO_TICKS for p in tick)
    assert all(p.timeline_ticks >= 0 for p in tick)


@needs_demo
def test_demo_placements_follow_the_source_order(demo):
    """Split regions placed on one track keep their source order.

    This is the evidence that the position field is a timeline coordinate and
    not, say, another copy of the source offset: two pieces of one recording
    appear in the same order they appear in the file, and for most pairs the
    timeline gap equals the source gap.
    """
    plain, blocks, be, _, regions = demo
    placements = extract_playlists(plain, blocks, be, regions)

    by_track_source = {}
    for p in placements:
        if p.unit != TIMELINE_UNIT_SAMPLES:
            continue
        r = regions[p.region_index]
        key = (p.track_name, r.source_index)
        by_track_source.setdefault(key, set()).add(
            (r.sample_offset, p.timeline_start))

    pairs = ordered = exact = 0
    for entries in by_track_source.values():
        entries = sorted(entries)
        for (off1, pos1), (off2, pos2) in zip(entries, entries[1:]):
            if off1 == off2:
                continue
            pairs += 1
            if pos1 <= pos2:
                ordered += 1
            if pos2 - pos1 == off2 - off1:
                exact += 1

    assert pairs >= 60
    assert ordered / pairs >= 0.95, f"{ordered}/{pairs} keep source order"
    assert exact / pairs >= 0.80, f"{exact}/{pairs} keep the exact gap"


@needs_demo
def test_demo_placements_are_monotonic_per_playlist(demo):
    """Within one 0x1052 map entry, sample positions never go backwards.

    Non-overlap is deliberately not asserted here: one map entry can carry
    several channels or playlists, so clips that overlap in time are legitimate.
    """
    plain, blocks, be, _, regions = demo
    placements = extract_playlists(plain, blocks, be, regions)
    by_playlist = {}
    for p in placements:
        if p.unit == TIMELINE_UNIT_SAMPLES:
            by_playlist.setdefault(p.playlist_offset, []).append(
                (p.block_offset, p.timeline_start))

    assert len(by_playlist) >= 30
    for entries in by_playlist.values():
        entries.sort()
        assert all(entries[i][1] <= entries[i + 1][1]
                   for i in range(len(entries) - 1))


@needs_demo
def test_demo_playlist_track_names_are_known_tracks():
    from conformkit.ptx import read_session

    s = read_session(DEMO_PTX)
    names = {p.track_name for p in s.playlist_placements}
    assert names
    assert names <= set(s.track_names), sorted(names - set(s.track_names))


@needs_demo
def test_legacy_session_has_no_placements():
    """0x1004 is the audio-file list, not a playlist: nothing should come out."""
    path = os.path.join(LEGACY_DIR, "RockBand.pts")
    if not os.path.exists(path):
        pytest.skip("legacy PTS fixture not present")
    plain, _ = decrypt(open(path, "rb").read())
    be = bool(plain[0x11])
    blocks = parse_blocks(plain, big_endian=be)
    assert extract_playlists(plain, blocks, be) == []


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
def test_prod_placements_are_decoded(prod):
    plain, blocks, be, _, regions = prod
    placements = extract_playlists(plain, blocks, be, regions)

    assert len(placements) == 7325
    assert all(p.unit == TIMELINE_UNIT_SAMPLES for p in placements)
    resolved = [p for p in placements if 0 <= p.region_index < len(regions)]
    assert len(resolved) == 7320        # five ids no longer resolve

    extent = max(r.end for r in regions)
    assert all(p.timeline_start < extent for p in resolved)


@needs_prod
def test_prod_placements_are_monotonic_per_playlist(prod):
    plain, blocks, be, _, regions = prod
    placements = extract_playlists(plain, blocks, be, regions)
    by_playlist = {}
    for p in placements:
        by_playlist.setdefault(p.playlist_offset, []).append(
            (p.block_offset, p.timeline_start))

    ordered = 0
    for entries in by_playlist.values():
        entries.sort()
        ordered += all(entries[i][1] <= entries[i + 1][1]
                       for i in range(len(entries) - 1))

    # Document order is a property of the file, not something we sort here.
    assert ordered / len(by_playlist) >= 0.95


@needs_prod
def test_playlists_scan_the_map_not_the_audio_file_list(prod):
    """The old implementation returned junk from 0x1004; the map is 0x1054."""
    plain, blocks, be, _, regions = prod
    placements = extract_playlists(plain, blocks, be, regions)
    assert placements
    assert all(p.block_offset for p in placements)
    assert all(p.region_name for p in placements
               if 0 <= p.region_index < len(regions))
    # 0x1004 also appears in the session; it must not contribute placement
    # records, or the count above would be far larger.
    assert len(placements) < 10_000
