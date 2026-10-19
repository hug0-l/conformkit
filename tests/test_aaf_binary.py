"""Binary AAF container tests.

A real AAF fixture (an Atmos lock-cut handover, 8.7 MB) drives the meaningful
assertions; synthetic compound files cover the CFB reader's edge cases. The
real fixture is not committed, so those tests skip when it is absent.
"""
import os
import struct

import pytest

from conformkit.aaf import AafContainer, identify_aaf, read_aaf_names
from conformkit.cfb import CFB_MAGIC, Cfb, CfbError

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "production")
REAL_AAF = os.path.join(FIXTURE_DIR, "GP04_LockCut.aaf")

needs_real = pytest.mark.skipif(
    not os.path.exists(REAL_AAF), reason="production AAF fixture not present")


# --------------------------------------------------------------- builders

def make_cfb(major: int = 4, minor: int = 62, sector_shift: int = 12,
             extra: bytes = b"") -> bytes:
    """A minimal but structurally valid compound file header."""
    sector = 1 << sector_shift
    header = bytearray(512)
    header[0:8] = CFB_MAGIC
    struct.pack_into("<H", header, 24, minor)
    struct.pack_into("<H", header, 26, major)
    struct.pack_into("<H", header, 28, 0xFFFE)      # little endian
    struct.pack_into("<H", header, 30, sector_shift)
    struct.pack_into("<H", header, 32, 6)           # 64-byte mini sectors
    struct.pack_into("<I", header, 44, 0)           # num FAT sectors
    struct.pack_into("<I", header, 48, 0xFFFFFFFE)  # no directory chain
    struct.pack_into("<I", header, 56, 4096)        # mini cutoff
    struct.pack_into("<I", header, 60, 0xFFFFFFFE)  # no mini FAT
    struct.pack_into("<I", header, 64, 0)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)  # no DIFAT
    struct.pack_into("<I", header, 72, 0)
    for i in range(109):
        struct.pack_into("<I", header, 76 + 4 * i, 0xFFFFFFFF)
    total = (len(header) + sector - 1) // sector * sector
    return bytes(header).ljust(total, b"\x00") + extra


# --------------------------------------------------------------- identify

def test_identify_rejects_non_cfb():
    info = identify_aaf(b"not a compound file at all")
    assert info["is_aaf"] is False
    assert "OLE2" in info["reason"]


def test_identify_accepts_cfb():
    info = identify_aaf(make_cfb())
    assert info["is_aaf"] is True
    assert info["aaf_version"] == "4.62"
    assert info["sector_size"] == 4096


def test_cfb_rejects_bad_magic():
    with pytest.raises(CfbError):
        Cfb(b"\x00" * 512)


def test_cfb_rejects_big_endian():
    data = bytearray(make_cfb())
    struct.pack_into("<H", data, 28, 0xFEFF)
    with pytest.raises(CfbError, match="big-endian"):
        Cfb(bytes(data))


def test_cfb_header_fields():
    c = Cfb(make_cfb(major=3, minor=0, sector_shift=9))
    assert c.major == 3 and c.minor == 0
    assert c.sector_size == 512
    assert c.mini_size == 64
    assert c.mini_cutoff == 4096


# --------------------------------------------------------------- names

def test_names_empty_on_no_utf16():
    # a compound file with no UTF-16 text yields nothing
    assert read_aaf_names(make_cfb()) == []


def test_names_finds_utf16():
    text = "B003C2889A01".encode("utf-16-le")
    assert "B003C2889A01" in read_aaf_names(b"\x00" * 64 + text + b"\x00" * 64)


def test_names_skips_container_boilerplate():
    blob = (b"\x00" * 32
            + "Root Entry".encode("utf-16-le")
            + b"\x00" * 32
            + "properties".encode("utf-16-le")
            + b"\x00" * 32
            + "RollName".encode("utf-16-le")
            + b"\x00" * 32)
    names = read_aaf_names(blob)
    assert "RollName" in names
    assert "Root Entry" not in names
    assert "properties" not in names


def test_names_are_deduplicated():
    text = "Scene12".encode("utf-16-le")
    names = read_aaf_names(text + b"\x00" * 8 + text)
    assert names.count("Scene12") == 1


def test_names_requires_letters():
    # digits and punctuation alone are serialisation noise, not names
    assert read_aaf_names("12345".encode("utf-16-le")) == []


# --------------------------------------------------------------- real file

@needs_real
def test_real_identifies_as_aaf(real_aaf_bytes):
    info = identify_aaf(real_aaf_bytes)
    assert info["is_aaf"] is True
    assert info["looks_like_aaf"] is True
    assert info["sector_size"] == 4096


@needs_real
def test_real_has_smpte_keys(real_aaf_bytes):
    """AAF objects are keyed by SMPTE Universal Labels."""
    from conformkit.cfb import SMPTE_UL_PREFIX
    assert real_aaf_bytes.count(SMPTE_UL_PREFIX) > 1000


@needs_real
def test_real_streams_listed(real_container):
    streams = real_container.streams()
    assert len(streams) >= 10
    names = {e.name for e in streams}
    assert "properties" in names


@needs_real
def test_real_aaf_structure_present(real_container):
    names = {e.name for e in real_container.directory_all()}
    assert any(n.startswith("Properties-") for n in names)
    assert any(n.startswith("ClassDefinitions-") for n in names)
    assert "MetaDictionary-1" in names


@needs_real
def test_real_names_recovered(real_container):
    names = real_container.names()
    assert len(names) > 500
    joined = "\n".join(names)
    # AAF class vocabulary must be present
    assert "InterchangeObject" in joined
    assert "DataDefinition" in joined


@needs_real
def test_real_name_extraction_terminates(real_container):
    """Guards against an unbounded scan on an 8 MB container."""
    import time
    start = time.perf_counter()
    real_container.names()
    assert time.perf_counter() - start < 10.0


@pytest.fixture(scope="module")
def real_aaf_bytes():
    return open(REAL_AAF, "rb").read()


@pytest.fixture(scope="module")
def real_container():
    return AafContainer(REAL_AAF)
