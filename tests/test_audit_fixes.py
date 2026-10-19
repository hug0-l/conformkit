"""Regression tests for defects found in the independent audit.

Each test encodes the failure mode, not just the fix: the point is that the
broken behaviour cannot come back silently.
"""
import os
import struct

import pytest

from conformkit.aaf import identify_aaf
from conformkit.aafwrite import available, write_binary_aaf
from conformkit.bwf import read_wav_info
from conformkit.cfb import CFB_MAGIC, Cfb, CfbError
from conformkit.edl import parse_edl
from conformkit.midi import MidiSettings, build_smf, frames_to_ticks
from conformkit.model import EditList, Event, Transition, parse_track_text
from conformkit.ptblocks import HEADER_SIZE, parse_blocks
from conformkit.ptdecrypt import is_decrypted
from conformkit.ptregions import _decode_triple, extract_audio_files, \
    extract_regions
from conformkit.timecode import Timecode, add, rate_for, to_frames

R25 = rate_for("25")
HERE = os.path.dirname(__file__)
PTX_FIXTURES = os.path.join(HERE, "..", "fixtures", "ptx")


# --------------------------------------------------------------- builders

def make_block(content_type, payload=b"", block_type=1, big_endian=False,
               size=None):
    """One block, using the on-disk convention: size counts from the ct field."""
    fmt = ">" if big_endian else "<"
    if size is None:
        size = 2 + len(payload)
    return b"Z" + struct.pack(fmt + "HIH", block_type, size, content_type) + payload


def make_cfb(fat, dir_start=0, sector_shift=9, mini_shift=6):
    hdr = bytearray(512)
    hdr[:8] = CFB_MAGIC
    struct.pack_into("<HHHHH", hdr, 24, 0x3E, 4, 0xFFFE, sector_shift,
                     mini_shift)
    struct.pack_into("<I", hdr, 44, 1)            # one FAT sector
    struct.pack_into("<I", hdr, 48, dir_start)
    struct.pack_into("<I", hdr, 56, 4096)
    struct.pack_into("<I", hdr, 60, 0xFFFFFFFE)
    struct.pack_into("<I", hdr, 68, 0xFFFFFFFE)
    for i in range(109):
        struct.pack_into("<I", hdr, 76 + 4 * i, 0xFFFFFFFF)
    struct.pack_into("<I", hdr, 76, 1)            # FAT lives in sector 1
    entries = list(fat) + [0xFFFFFFFF] * (128 - len(fat))
    return bytes(hdr) + b"\x00" * 512 + struct.pack("<128I", *entries)


def make_wav(chunks):
    body = b"".join(chunks)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def wav_fmt(channels=1, rate=48000, bits=24):
    block = channels * (bits // 8)
    return b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, channels, rate, rate * block, block, bits)


def ev(start=0, dur=100, track="V", roll="R1", clip="C1", src=None):
    return Event(
        rec_in=add(Timecode(0, 0, 0, 0), start, R25),
        rec_out=add(Timecode(0, 0, 0, 0), start + dur, R25),
        src_in=add(Timecode(0, 0, 0, 0), start + 1000, R25),
        src_out=add(Timecode(0, 0, 0, 0), start + dur + 1000, R25),
        roll_name=roll, clip_name=clip, source_file=src or clip,
        tracks=parse_track_text(track), transition=Transition("C", 0),
    )


# --------------------------------------------------------------- decryption

def test_is_decrypted_rejects_encrypted_body():
    """A byte histogram called encrypted data plaintext; structure cannot."""
    plain = b"\x00" * 0x1F + make_block(0x1004) * 4
    assert is_decrypted(plain)

    # XOR the body: the byte distribution stays similar, the chain does not
    encrypted = plain[:0x14] + bytes(b ^ 0xFF for b in plain[0x14:])
    assert not is_decrypted(encrypted)


def test_is_decrypted_rejects_random_bytes():
    import random

    rng = random.Random(1234)
    noise = bytes(rng.randrange(256) for _ in range(4096))
    assert not is_decrypted(noise)


# --------------------------------------------------------------- blocks

def test_block_header_read_needs_nine_bytes():
    """An 8-byte tail must not raise; the content type is the 9th byte."""
    assert parse_blocks(b"", start=0) == []
    eight = b"Z" + b"\x00" * 7
    assert parse_blocks(eight, start=0) == []
    assert parse_blocks(eight + b"\x00" * 40, start=0) == []


def test_children_never_start_inside_the_header():
    """A block type of 0x005A must not be mistaken for a nested 'Z'."""
    payload = b"\x00" * 64
    parent = make_block(0x1004, payload, block_type=0x005A)
    blocks = parse_blocks(parent, start=0)
    assert len(blocks) == 1
    for child in blocks[0].children:
        assert child.offset >= blocks[0].offset + HEADER_SIZE


# --------------------------------------------------------------- regions

def test_big_endian_triple_uses_reversed_width_bytes():
    data = bytearray(32)
    # widths, in value order, sit at base+4, base+3, base+2 for big-endian
    data[4] = 0x20
    data[3] = 0x20
    data[2] = 0x10
    data[5:7] = struct.pack("<H", 1000)
    data[7:9] = struct.pack("<H", 500)
    data[9:10] = struct.pack("<B", 200)

    assert _decode_triple(bytes(data), 0, big_endian=True) == (1000, 500, 200)


def test_reliable_is_unknown_without_a_source_length():
    """No source length means no verdict, not an optimistic True."""
    payload = bytearray(64)
    payload[9:13] = struct.pack("<I", 5)          # name length
    payload[13:18] = b"clip1"                      # name
    base = 18                                      # ct+15+len(name)
    payload[base + 1] = 0x20
    payload[base + 2] = 0x20
    payload[base + 3] = 0x10
    payload[base + 5:base + 7] = struct.pack("<H", 10)
    payload[base + 7:base + 9] = struct.pack("<H", 20)
    payload[base + 9] = 5

    record = make_block(0x2629, bytes(payload))
    parent = make_block(0x262A, record)
    data = parent
    blocks = parse_blocks(data, start=0)
    region = extract_regions(data, blocks, False)[0]
    assert region.name == "clip1"
    assert (region.sample_offset, region.length, region.start) == (10, 20, 5)
    assert region.reliable is None


def test_big_endian_audio_file_record():
    be = True
    info = struct.pack(">I", 48000) + b"\x02\x18" + struct.pack(">Q", 123456)
    info_block = make_block(0x1001, info, big_endian=be)

    names = bytearray(13)
    names[5:9] = struct.pack(">I", 1)             # count
    names[9:13] = struct.pack(">I", 0)            # header length
    names += b"\x00" * 9
    names += struct.pack(">I", len("BE_FILE.wav"))
    names += b"BE_FILE.wav"
    name_block = make_block(0x103A, bytes(names), big_endian=be)

    meta = make_block(0x1003, info_block, big_endian=be)
    data = make_block(0x1004, name_block + meta, big_endian=be)
    blocks = parse_blocks(data, start=0, big_endian=be)

    files = extract_audio_files(data, blocks, be)
    assert len(files) == 1
    assert files[0].name == "BE_FILE.wav"
    assert files[0].sample_rate == 48000
    assert files[0].channels == 2
    assert files[0].bits == 24
    assert files[0].length == 123456


# --------------------------------------------------------------- names

def test_big_endian_session_names_are_recovered():
    """The committed .pts fixtures store length prefixes big-endian.

    This fixture carries 124 names in ``0x1021`` blocks (``Guitar Amp 1``,
    ``Mesa Boogie``...). Reading the prefix little-endian rejects every one, so
    the assertion is that the big-endian read finds them.
    """
    path = os.path.join(PTX_FIXTURES, "RockBand.pts")
    if not os.path.exists(path):
        pytest.skip("fixture missing")

    from conformkit.ptblocks import walk
    from conformkit.ptdecrypt import decrypt
    from conformkit.ptx import _pstring_at

    plain, _ = decrypt(open(path, "rb").read())
    blocks = parse_blocks(plain, big_endian=True)

    found = []
    for _, block in walk(blocks):
        for d in range(0, 16):
            name = _pstring_at(plain, block.payload_offset + d, True)
            if name:
                found.append(name)
                break

    assert len(found) > 100
    assert any("Guitar" in n for n in found)


# --------------------------------------------------------------- cfb

def test_cyclic_fat_is_rejected():
    cfb = Cfb(make_cfb([0]))
    with pytest.raises(CfbError, match="cyclic"):
        cfb.directory()


def test_bad_sector_shift_is_rejected():
    with pytest.raises(CfbError, match="sector shift"):
        Cfb(make_cfb([0xFFFFFFFF], sector_shift=3))


def test_bad_mini_shift_is_rejected():
    with pytest.raises(CfbError, match="mini sector shift"):
        Cfb(make_cfb([0xFFFFFFFF], mini_shift=3))


def test_non_cfb_is_rejected():
    with pytest.raises(CfbError):
        Cfb(b"\x00" * 600)
    assert identify_aaf(b"\x00" * 600)["is_aaf"] is False


# --------------------------------------------------------------- midi

def test_note_duration_uses_the_event_length():
    start = to_frames(Timecode(1, 0, 0, 0), R25)   # one hour in
    events = [ev(start=start, dur=25)]
    smf = build_smf(EditList(events=events, rate=R25, title="L"), R25,
                    MidiSettings(note_duration_of_event=True,
                                 marker_track=False))
    on, off = _note_ticks(smf)
    assert off - on == frames_to_ticks(25, R25)


def test_invalid_note_number_is_rejected():
    with pytest.raises(ValueError, match="note_number"):
        build_smf(EditList(events=[ev()], rate=R25, title="L"), R25,
                  MidiSettings(note_number=200))


def _note_ticks(smf):
    """Return the absolute ticks of the first note-on/note-off pair."""
    pos, tick, on, off = 14, 0, None, None
    while pos + 8 <= len(smf):
        length = struct.unpack(">I", smf[pos + 4:pos + 8])[0]
        body = smf[pos + 8:pos + 8 + length]
        i = 0
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
                i += 2
                n = body[i]
                i += 1 + n
                continue
            if status == 0x90:
                if on is None:
                    on = tick
                i += 3
            elif status == 0x80:
                if off is None and on is not None:
                    return on, tick
                i += 3
            else:
                i += 3
        pos += 8 + length
    raise AssertionError("no note pair found")


# --------------------------------------------------------------- bwf

def test_wav_without_pad_byte_keeps_the_next_chunk():
    """A shifted id ('ata0') is printable, so printability alone is unsafe."""
    ixml = b"<BWFXML>" + b"x" * 26 + b"</BWFXML>"     # 35 bytes, odd, no pad
    data = b"\x00" * 24                                # 8 frames at 24-bit mono
    wav = make_wav([
        wav_fmt(),
        b"iXML" + struct.pack("<I", len(ixml)) + ixml,
        b"data" + struct.pack("<I", len(data)) + data,
    ])
    info = read_wav_info(data=wav)
    assert info.duration_frames == 8


# --------------------------------------------------------------- aaf

needs_aaf2 = pytest.mark.skipif(not available(), reason="pyaaf2 not installed")


@needs_aaf2
def test_source_clips_reference_their_source_mob(tmp_path):
    edl = os.path.join(HERE, "..", "fixtures", "test_25.edl")
    if not os.path.exists(edl):
        pytest.skip("EDL fixture missing")
    with open(edl, encoding="utf-8", errors="replace") as fh:
        edit_list = parse_edl(fh.read(), R25, "CMX3600",
                              strict_roll_length=False)

    out = tmp_path / "links.aaf"
    write_binary_aaf(str(out), edit_list, R25, title="links")

    import aaf2
    with aaf2.open(str(out), "r") as f:
        comp = next(m for m in f.content.mobs if m.name == "links")
        clips = [c for s in comp.slots for c in _clips(s.segment)]
        assert clips
        for clip in clips:
            assert "00000000.00000000.00000000.00000000" not in str(clip.mob_id)
            assert clip.slot is not None, "SourceClip points at no mob"


@needs_aaf2
def test_audio_and_video_do_not_share_a_source_mob(tmp_path):
    events = [ev(track="V", clip="SHARED", dur=50),
              ev(track="A1", clip="SHARED", dur=50)]
    out = tmp_path / "av.aaf"
    write_binary_aaf(str(out), EditList(events=events, rate=R25, title="av"),
                     R25, title="av")

    import aaf2
    with aaf2.open(str(out), "r") as f:
        named = [m for m in f.content.mobs if m.name == "SHARED"]
        assert len(named) == 2
        kinds = {type(m.descriptor).__name__ for m in named}
        assert kinds == {"PCMDescriptor", "CDCIDescriptor"}


def _clips(segment):
    if segment is None:
        return
    for comp in getattr(segment, "components", []):
        if type(comp).__name__ == "SourceClip":
            yield comp
        else:
            yield from _clips(comp)
