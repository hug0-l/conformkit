"""Pro Tools session file tests.

Two fixture sets are used:

  * synthetic files built to exercise the identifier and the string decoder
    without depending on any download;
  * a real production session (`GP04_DSS1.ptx`, 34 MB) from a Dolby Atmos
    sound-to-editor handover, used to validate the parser against a file the
    author of this project did not create.

The real fixture is not committed; these tests skip when it is absent.
"""
import os
import struct
import xml.etree.ElementTree as ET

import pytest

from conformkit.ptx import (
    MAGIC_BITSTRING, PRONOM, SIGNATURE_PTF_PTS, SIGNATURE_PTX, PTXError,
    extract_strings, extract_xml_documents, identify, read_session,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "production")
REAL_PTX = os.path.join(FIXTURE_DIR, "GP04_DSS1.ptx")
LEGACY_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "ptx")
ROCKBAND_PTS = os.path.join(LEGACY_DIR, "RockBand.pts")

needs_real = pytest.mark.skipif(
    not os.path.exists(REAL_PTX), reason="production PTX fixture not present")
needs_legacy = pytest.mark.skipif(
    not os.path.exists(ROCKBAND_PTS), reason="legacy PTS fixture not present")


# --------------------------------------------------------------- builders

def make_ptx(body: bytes = b"", version: bytes = b"\x00\x05\xeb") -> bytes:
    return b"\x03" + MAGIC_BITSTRING + version + SIGNATURE_PTX + body


def make_ptf(body: bytes = b"") -> bytes:
    """A PTF/PTS file. The 01 01 signature sits at offset 17, so there is no
    separate version field before it."""
    return b"\x03" + MAGIC_BITSTRING + SIGNATURE_PTF_PTS + body


@pytest.fixture(scope="module")
def real_data():
    """Read the 34 MB fixture once; it is used by eight tests."""
    return open(REAL_PTX, "rb").read()


@pytest.fixture(scope="module")
def real_session():
    return read_session(REAL_PTX)


def pstr(text: str) -> bytes:
    """A length-prefixed string as the format stores it."""
    raw = text.encode("ascii")
    return struct.pack("<I", len(raw)) + raw


# --------------------------------------------------------------- identify

def test_identifies_ptx():
    fmt = identify(make_ptx(), "x.ptx")
    assert fmt.kind == "PTX"
    assert fmt.pronom == "fmt/1727"
    assert fmt.is_pro_tools


def test_identifies_ptf_by_extension():
    fmt = identify(make_ptf(), "x.ptf")
    assert fmt.kind == "PTF"
    assert fmt.pronom == "fmt/1951"


def test_identifies_pts_by_extension():
    fmt = identify(make_ptf(), "x.pts")
    assert fmt.kind == "PTS"
    assert fmt.pronom == "fmt/1951"


def test_ptf_pts_share_a_signature():
    """PRONOM fmt/1951 covers both, so the extension is the tie-breaker."""
    fmt = identify(make_ptf(), "no_extension")
    assert fmt.kind in ("PTF", "PTS")
    assert "extension absent" in fmt.confidence


def test_rejects_random_data():
    assert identify(b"\x00" * 100).kind == "unknown"
    assert identify(os.urandom(100)).kind == "unknown"


def test_rejects_bad_block_marker():
    data = make_ptx()
    data = b"\x04" + data[1:]
    assert identify(data).kind == "unknown"


def test_rejects_bad_bitstring():
    data = bytearray(make_ptx())
    data[5] = ord("X")
    assert identify(bytes(data)).kind == "unknown"


def test_rejects_short_file():
    assert identify(b"\x03" + MAGIC_BITSTRING).confidence == "too short"


def test_version_bytes_are_reported():
    fmt = identify(make_ptx(version=b"\x00\x05\xeb"))
    assert fmt.version_bytes == b"\x00\x05\xeb"


# --------------------------------------------------------------- strings

def test_extract_strings_roundtrip():
    data = make_ptx(pstr("Info #1") + pstr("Track Name") + pstr("A1"))
    got = [t for _, t in extract_strings(data)]
    assert "Info #1" in got
    assert "Track Name" in got
    assert "A1" in got


def test_extract_strings_rejects_non_text():
    # a length that points at binary must not be reported as a string
    data = make_ptx(struct.pack("<I", 8) + b"\x01\x02\x03\x04\x05\x06\x07\x08")
    assert all("\x01" not in t for _, t in extract_strings(data))


def test_extract_strings_rejects_nul_bytes():
    data = make_ptx(struct.pack("<I", 6) + b"ab\x00cde")
    assert not any(t == "ab\x00cde" for _, t in extract_strings(data))


def test_extract_strings_empty_body_ignored():
    data = make_ptx(struct.pack("<I", 0) + b"ABCDEF")
    assert not any(t == "" for _, t in extract_strings(data))


# --------------------------------------------------------------- XML

def test_extract_plain_xml():
    xml = b"<dict>" + b"<key>Name</key><string>Hello</string>" * 6 + b"</dict>"
    data = make_ptx(b"\x00" * 32 + xml + b"\x00" * 32)
    docs = extract_xml_documents(data)
    assert len(docs) == 1
    off, chunk, tag, was_xor = docs[0]
    assert tag == "dict"
    assert was_xor is False
    ET.fromstring(chunk)


def test_extract_xor_xml():
    xml = b"<dict>" + b"<key>Name</key><string>Hello</string>" * 6 + b"</dict>"
    enc = bytes(b ^ 0xA1 for b in xml)
    data = make_ptx(b"\x00" * 32 + enc + b"\x00" * 32)
    docs = extract_xml_documents(data)
    assert len(docs) == 1
    off, chunk, tag, was_xor = docs[0]
    assert was_xor is True
    assert chunk == xml
    ET.fromstring(chunk)


def test_extract_rejects_malformed_xml():
    bad = b"<dict>" + b"<key>Name</key>" * 20   # never closed
    data = make_ptx(b"\x00" * 32 + bad + b"\x00" * 32)
    assert extract_xml_documents(data) == []


def test_nested_dicts_are_balanced():
    """A dict containing a dict must not be cut short at the inner close."""
    inner = b"<dict><key>a</key><string>b</string>" + b"<key>pad</key>" * 8 + b"</dict>"
    outer = b"<dict><key>outer</key><string>x</string>" + b"<key>p2</key>" * 8 \
            + inner + b"</dict>"
    data = make_ptx(b"\x00" * 32 + outer + b"\x00" * 32)
    docs = extract_xml_documents(data)
    assert len(docs) >= 1
    ET.fromstring(docs[0][1])
    assert b"</dict>" in docs[0][1]


# --------------------------------------------------------------- legacy names

@needs_legacy
def test_legacy_audio_track_names_are_tracks():
    """0x1014 is the audio track list; it must not be filed as a bus."""
    s = read_session(ROCKBAND_PTS)
    assert s.fmt.kind == "PTS"
    assert len(s.track_names) == 15
    assert s.track_names[:3] == ["Guitar Amp 1", "Guitar DI 1", "Guitar Amp 2"]


@needs_legacy
def test_legacy_io_names_are_buses():
    """0x1021 is the I/O channel list, so its names belong in bus_names."""
    s = read_session(ROCKBAND_PTS)
    assert "Mesa Boogie" in s.bus_names
    assert "A 1-2" in s.bus_names
    assert "A 1-2" not in s.track_names
    assert s.track_names and s.bus_names


# --------------------------------------------------------------- real file

@needs_real
def test_real_file_identified(real_data):
    fmt = identify(real_data, "GP04_DSS1.ptx")
    assert fmt.kind == "PTX"
    assert fmt.pronom == PRONOM["PTX"]


@needs_real
def test_real_file_rejects_ptf_signature(real_data):
    """The PTX tail is not the PTF/PTS tail: the identifier must not confuse them."""
    data = real_data
    assert data[20:24] == SIGNATURE_PTX
    assert data[17:19] != SIGNATURE_PTF_PTS


@needs_real
def test_real_strings_found(real_data):
    strings = [t for _, t in extract_strings(real_data)]
    assert len(strings) > 400
    assert "Info #1" in strings


@needs_real
def test_real_session_metadata(real_session):
    s = real_session
    assert s.fmt.kind == "PTX"
    assert s.session_name == "2223GP04_DSS1"
    assert s.session_file.endswith(".ptx")
    assert s.user_name
    assert s.size > 30_000_000


@needs_real
def test_real_xml_documents_all_parse(real_session):
    """Every recovered document must be accepted by a real XML parser."""
    s = real_session
    assert len(s.xml_documents) >= 10
    for off, chunk, tag, was_xor in s.xml_documents:
        ET.fromstring(chunk)


@needs_real
def test_real_xml_after_full_decryption(real_session):
    """Once the whole body is decrypted, every document is plaintext.

    The earlier per-window "XOR region" observation was the file-level
    encryption, not a second layer: `conformkit.ptdecrypt` removes it for the
    whole body, so no document should need further de-obfuscation.
    """
    s = real_session
    from_xor = [d for d in s.xml_documents if d[3]]
    from_raw = [d for d in s.xml_documents if not d[3]]
    assert from_raw, "expected plaintext documents after decryption"
    assert not from_xor, "no document should still need XOR after decryption"


@needs_real
def test_real_xml_contains_plugin_parameters(real_session):
    s = real_session
    body = b"".join(chunk for _, chunk, _, _ in s.xml_documents)
    assert b"<key>Name</key>" in body
    assert b"<real>" in body or b"<integer>" in body


@needs_real
def test_real_track_names_present(real_session):
    s = real_session
    joined = "\n".join(s.track_names)
    # an Atmos session: bed and object masters
    assert "Mx BED Master" in joined or "Mx Obj Master" in joined


@needs_real
def test_real_file_performance():
    import time
    start = time.perf_counter()
    read_session(REAL_PTX)
    elapsed = time.perf_counter() - start
    assert elapsed < 30.0, f"34 MB session took {elapsed:.1f}s"


# --------------------------------------------------------------- errors

def test_read_session_rejects_non_session(tmp_path):
    p = tmp_path / "not.ptx"
    p.write_bytes(b"not a pro tools session at all, really")
    with pytest.raises(PTXError):
        read_session(str(p))
