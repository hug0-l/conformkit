"""PTX safe-write tests.

The writer must never corrupt a session. Every test asserts structural
integrity after the edit, and that the source file's bytes are unchanged.
"""
import hashlib
import os
import shutil

import pytest

from conformkit.ptdecrypt import decrypt
from conformkit.ptblocks import parse_blocks, walk
from conformkit.ptwrite import (
    PtxWriteError, RenameReport, rename_names, same_length_renames,
    write_clone,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                       "production", "GP04_DSS1.ptx")
needs_real = pytest.mark.skipif(not os.path.exists(FIXTURE),
                                reason="production PTX not present")


def digest(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------- validation

def test_same_length_filter_accepts_equal():
    ok, rejected = same_length_renames({"Lock_Reference": "LOCK_REFERENCE"})
    assert ok == {"Lock_Reference": "LOCK_REFERENCE"}
    assert rejected == []


def test_same_length_filter_rejects_length_change():
    ok, rejected = same_length_renames({"A1": "Audio1"})
    assert ok == {}
    assert rejected and "length differs" in rejected[0]


def test_same_length_filter_rejects_empty():
    ok, rejected = same_length_renames({"A1": ""})
    assert ok == {}
    assert rejected


def test_refuses_to_overwrite_source(tmp_path):
    src = tmp_path / "s.ptx"
    src.write_bytes(b"\x03" + b"0" * 64)
    with pytest.raises(PtxWriteError, match="over the source"):
        write_clone(str(src), str(src), {})


def test_refuses_existing_destination(tmp_path):
    src = tmp_path / "s.ptx"
    dst = tmp_path / "d.ptx"
    src.write_bytes(b"\x00" * 64)
    dst.write_bytes(b"\x00" * 64)
    with pytest.raises(PtxWriteError, match="exists"):
        write_clone(str(src), str(dst), {})


# --------------------------------------------------------------- real file

@needs_real
def test_rename_preserves_structure(tmp_path):
    dst = tmp_path / "renamed.ptx"
    before = digest(FIXTURE)
    report = write_clone(FIXTURE, str(dst),
                         {"Lock_Reference": "LOCK_REFERENCE"})

    assert report.verified is True
    assert report.blocks_before == report.blocks_after
    assert report.renamed >= 1
    assert dst.stat().st_size == os.path.getsize(FIXTURE)

    # the source must be byte-identical afterwards
    assert digest(FIXTURE) == before


@needs_real
def test_renamed_session_still_parses(tmp_path):
    dst = tmp_path / "renamed2.ptx"
    write_clone(FIXTURE, str(dst),
                {"Lock_Reference": "LOCK_REFERENCE"})

    plain, _ = decrypt(dst.read_bytes())
    blocks = parse_blocks(plain, big_endian=bool(plain[0x11]))
    total = sum(1 for _ in walk(blocks))
    assert total > 10_000
    assert b"LOCK_REFERENCE" in plain


@needs_real
def test_unknown_name_is_skipped(tmp_path):
    dst = tmp_path / "skip.ptx"
    report = write_clone(FIXTURE, str(dst),
                         {"NOSUCHNAME!!": "NOSUCHNAME_2"})
    assert report.renamed == 0
    assert report.skipped >= 1
    assert report.verified is True


@needs_real
def test_length_changing_rename_rejected(tmp_path):
    dst = tmp_path / "bad.ptx"
    report = rename_names(FIXTURE, str(dst), {"A1": "Audio 1"})
    assert report.renamed == 0
    assert report.skipped >= 1
    assert "length differs" in report.skipped_reasons[0]


@needs_real
def test_multiple_renames(tmp_path):
    dst = tmp_path / "multi.ptx"
    report = write_clone(FIXTURE, str(dst), {
        "Lock_Reference": "LOCK_REFERENCE",
        "Master VCA": "MASTER VCA",
    })
    assert report.verified
    plain, _ = decrypt(dst.read_bytes())
    assert b"LOCK_REFERENCE" in plain
    assert b"MASTER VCA" in plain


@needs_real
def test_summary_text():
    r = RenameReport(renamed=3, skipped=1, verified=True)
    text = r.summary()
    assert "3 renamed" in text
    assert "verified" in text
