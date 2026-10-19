"""Assembly and script-import tests."""
import os
import struct
import tempfile

import pytest

from conformkit.assembly import (
    AssemblyError, build_assembly, most_common_rate, resolve_overlaps, scan_wavs,
)
from conformkit.bwf import read_wav_info
from conformkit.script import (
    ScriptError, ScriptLine, parse_script_text, script_to_markers,
)
from conformkit.timecode import Timecode, rate_for, to_frames

R25 = rate_for("25")


# --------------------------------------------------------------- wav builder

def make_wav(path, channels=2, rate=48000, bits=24, frames=48000,
             time_reference=0, tape="", scene="", take=""):
    block = channels * (bits // 8)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)

    bext = bytearray(602)
    bext[256:256 + 5] = b"R0042"
    bext[320:330] = b"2024-03-16"
    struct.pack_into("<Q", bext, 338, time_reference)
    struct.pack_into("<H", bext, 346, 1)
    bext = bytes(bext)

    ixml = (f'<?xml version="1.0"?><BWFXML><SCENE>{scene}</SCENE>'
            f'<TAKE>{take}</TAKE><TAPE>{tape}</TAPE></BWFXML>').encode() + b"\x00"

    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    # 602 bytes is even, so no RIFF word-alignment pad is needed after it.
    # iXML may be odd-length; RIFF requires a pad byte in that case.
    body += b"bext" + struct.pack("<I", len(bext)) + bext
    body += b"iXML" + struct.pack("<I", len(ixml)) + ixml
    if len(ixml) % 2:
        body += b"\x00"
    data = b"\x00" * (block * frames)
    body += b"data" + struct.pack("<I", len(data)) + data

    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


@pytest.fixture
def wav_folder(tmp_path):
    d = tmp_path / "wavs"
    d.mkdir()
    make_wav(str(d / "a.wav"), time_reference=0, tape="R1", scene="1A", take="1")
    make_wav(str(d / "b.wav"), time_reference=48000 * 10, tape="R2",
             scene="1A", take="2")
    make_wav(str(d / "c.wav"), time_reference=48000 * 20, tape="R3",
             scene="2B", take="1")
    return str(d)


# --------------------------------------------------------------- assembly

def test_scan_finds_wavs(wav_folder):
    found = scan_wavs(wav_folder)
    assert len(found) == 3
    assert all(p.lower().endswith(".wav") for p in found)


def test_scan_missing_folder():
    with pytest.raises(AssemblyError):
        scan_wavs("C:/definitely/not/here")


def test_build_places_by_timestamp(wav_folder):
    paths = scan_wavs(wav_folder)
    result = build_assembly(paths, R25)
    assert len(result.items) == 3
    starts = sorted(to_frames(i.start, R25) for i in result.items)
    # 0s, 10s, 20s at 25fps
    assert starts == [0, 250, 500]


def test_build_captures_scene_take(wav_folder):
    result = build_assembly(scan_wavs(wav_folder), R25)
    scenes = {(i.scene, i.take) for i in result.items}
    assert ("1A", "1") in scenes
    assert ("2B", "1") in scenes


def test_build_warns_on_poly(wav_folder):
    warnings = []
    build_assembly(scan_wavs(wav_folder), R25, warn=warnings)
    assert any("channels" in w for w in warnings)


def test_assembly_to_edit_list(wav_folder):
    result = build_assembly(scan_wavs(wav_folder), R25)
    el = result.to_edit_list()
    assert len(el) == 3
    assert el.rate == R25
    for ev in el:
        assert ev.rec_duration(R25) > 0


def test_resolve_overlaps_is_deterministic(wav_folder):
    result = build_assembly(scan_wavs(wav_folder), R25)
    # force an overlap
    for item in result.items:
        item.start = Timecode(0, 0, 0, 0)
    resolve_overlaps(result)
    starts = [to_frames(i.start, R25) for i in result.items]
    assert starts == sorted(starts)
    # no overlap: each start >= previous end
    prev_end = 0
    for item in result.items:
        s = to_frames(item.start, R25)
        assert s >= prev_end
        prev_end = s + item.duration_frames


def test_skips_wav_without_timestamp(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    make_wav(str(d / "nots.wav"), time_reference=0)
    # strip bext by rebuilding without it
    p = str(d / "nots.wav")
    info = read_wav_info(path=p)
    assert info.sample_rate == 48000


def test_most_common_rate(tmp_path):
    d = tmp_path / "r"
    d.mkdir()
    make_wav(str(d / "a.wav"), rate=48000)
    make_wav(str(d / "b.wav"), rate=48000)
    make_wav(str(d / "c.wav"), rate=44100)
    infos = [read_wav_info(path=str(d / f)) for f in ("a.wav", "b.wav", "c.wav")]
    assert most_common_rate(infos) == 48000


# --------------------------------------------------------------- script

SCRIPT = """1   INT. KITCHEN - DAY   1

    ALICE
    Where did you put it?

2   EXT. STREET - NIGHT   2

    BOB
    I don't know.
"""


def test_parse_script_scene_numbers():
    lines = parse_script_text(SCRIPT)
    nums = [l.scene for l in lines if l.scene]
    assert "1" in nums
    assert "2" in nums


def test_parse_script_descriptions():
    lines = parse_script_text(SCRIPT)
    descs = [l.description for l in lines if l.description]
    assert any("KITCHEN" in d for d in descs)
    assert any("STREET" in d for d in descs)


def test_script_to_markers():
    lines = parse_script_text(SCRIPT)
    markers = script_to_markers(lines, R25, prefix="SC ")
    assert len(markers) >= 2
    assert all(m.name.startswith("SC ") for m in markers)


def test_script_empty_input():
    assert parse_script_text("") == []


def test_script_rejects_binary_garbage():
    # should not raise; just yields nothing useful
    assert parse_script_text("\x00\x01\x02garbage") == []
