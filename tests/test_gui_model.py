"""GUI view-model tests.

The window itself needs a display, but everything that decides what it shows is
plain data, so it is tested directly. These tests run headless.
"""
import os
import struct

import pytest

from conformkit.guimodel import (
    DETAIL_TABS, DocumentKind, columns_for, load_document, rows_for,
    summary_lines,
)
from conformkit.timecode import rate_for

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
PROD_DIR = os.path.join(FIXTURE_DIR, "production")
EDL_SAMPLE = os.path.join(FIXTURE_DIR, "test_25.edl")
REAL_PTX = os.path.join(PROD_DIR, "GP04_DSS1.ptx")
REAL_AAF = os.path.join(PROD_DIR, "GP04_LockCut.aaf")


def make_wav(path, channels=2, rate=48000, bits=24, frames=4800):
    block = channels * (bits // 8)
    fmt = struct.pack("<HHIIHH", 1, channels, rate, rate * block, block, bits)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    data = b"\x00" * (block * frames)
    body += b"data" + struct.pack("<I", len(data)) + data
    with open(path, "wb") as fh:
        fh.write(b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)


# --------------------------------------------------------------- sniffing

def test_sniffs_edl():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("fixture missing")
    doc = load_document(EDL_SAMPLE, "25")
    assert doc.kind == DocumentKind.EDL
    assert doc.ok


def test_sniffs_wav(tmp_path):
    p = tmp_path / "a.wav"
    make_wav(str(p))
    doc = load_document(str(p), "25")
    assert doc.kind == DocumentKind.WAV
    assert doc.ok


def test_sniffs_unknown(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00" * 200)
    doc = load_document(str(p), "25")
    assert doc.kind == DocumentKind.UNKNOWN
    assert not doc.ok
    assert "unrecognised" in doc.error


def test_missing_file_reports_error(tmp_path):
    doc = load_document(str(tmp_path / "nope.edl"), "25")
    assert not doc.ok
    assert doc.error


def test_progress_callback_runs():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("fixture missing")
    seen = []
    load_document(EDL_SAMPLE, "25", progress=lambda f, m: seen.append((f, m)))
    assert seen
    assert seen[-1][0] == 1.0


# --------------------------------------------------------------- EDL view

def test_edl_columns_and_rows():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("fixture missing")
    doc = load_document(EDL_SAMPLE, "25")
    cols = columns_for(doc)
    assert [c.key for c in cols][:3] == ["no", "roll", "track"]

    rows = rows_for(doc)
    assert len(rows) == len(doc.edit_list)
    assert len(rows[0]) == len(cols)


def test_edl_summary_has_counts():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("fixture missing")
    doc = load_document(EDL_SAMPLE, "25")
    labels = dict(summary_lines(doc))
    assert "events" in labels
    assert "rate" in labels
    assert labels["rate"].startswith("25")


def test_edl_clip_tab():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("fixture missing")
    doc = load_document(EDL_SAMPLE, "25")
    rows = rows_for(doc, "clips")
    assert all(isinstance(r[1], str) for r in rows)


def test_edl_rate_variants():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("fixture missing")
    doc = load_document(EDL_SAMPLE, "24")
    assert doc.rate.label == "24"


# --------------------------------------------------------------- WAV view

def test_wav_summary(tmp_path):
    p = tmp_path / "b.wav"
    make_wav(str(p), channels=4, rate=48000, frames=24000)
    doc = load_document(str(p), "25")
    labels = dict(summary_lines(doc))
    assert labels["channels"] == "4"
    assert labels["sample rate"] == "48000 Hz"

    rows = rows_for(doc)
    fields = dict(rows)
    assert fields["channels"] == "4"
    assert fields["sample rate"] == "48000 Hz"


# --------------------------------------------------------------- PTX view

@pytest.mark.skipif(not os.path.exists(REAL_PTX), reason="no production PTX")
def test_ptx_view():
    doc = load_document(REAL_PTX, "25")
    assert doc.kind == DocumentKind.PTX
    assert doc.ok

    labels = dict(summary_lines(doc))
    assert labels["kind"] == "Pro Tools PTX"
    assert labels["PRONOM"] == "fmt/1727"
    assert int(labels["tracks"].replace(",", "")) > 100

    tracks = rows_for(doc)
    assert len(tracks) == len(doc.session.track_names)

    clips = rows_for(doc, "clips")
    assert len(clips) == len(doc.session.clip_names)

    plugins = rows_for(doc, "plugins")
    assert len(plugins) == len(doc.session.plugins)


@pytest.mark.skipif(not os.path.exists(REAL_PTX), reason="no production PTX")
def test_ptx_tabs_declared():
    doc = load_document(REAL_PTX, "25")
    keys = [k for k, _ in DETAIL_TABS[doc.kind]]
    assert keys == ["main", "clips", "plugins", "xml"]


# --------------------------------------------------------------- AAF view

@pytest.mark.skipif(not os.path.exists(REAL_AAF), reason="no production AAF")
def test_aaf_view():
    doc = load_document(REAL_AAF, "25")
    assert doc.kind == DocumentKind.AAF
    assert doc.ok

    labels = dict(summary_lines(doc))
    assert labels["kind"] == "AAF (binary)"
    assert int(labels["streams"]) > 5

    streams = rows_for(doc)
    assert len(streams) > 5
    names = rows_for(doc, "names")
    assert len(names) > 100


# --------------------------------------------------------------- tabs

def test_tabs_declared_per_kind():
    assert DETAIL_TABS[DocumentKind.EDL][0][0] == "main"
    assert DETAIL_TABS[DocumentKind.WAV] == [("main", "Metadata")]
    assert DETAIL_TABS[DocumentKind.UNKNOWN] == []


def test_columns_empty_for_unknown():
    from conformkit.guimodel import LoadedDocument

    doc = LoadedDocument(path="x", kind=DocumentKind.UNKNOWN)
    assert columns_for(doc) == []
    assert rows_for(doc) == []


# --------------------------------------------------------------- gui import

def test_gui_module_imports_without_display():
    """The UI package must import cleanly even with no display attached."""
    pytest.importorskip("tkinter")
    from conformkit import gui

    assert hasattr(gui, "ConformKitApp")
    assert callable(gui.main)


def test_gui_does_not_import_at_package_level():
    """Importing conformkit must not pull in Tk."""
    import subprocess
    import sys

    code = ("import sys, conformkit; "
            "print('tkinter' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True,
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
