"""Binary AAF writing tests.

pyaaf2 is optional. When present the tests write a real AAF and then read it
back with our own CFB reader, which proves the output is a well-formed
compound file rather than merely a file that exists.
"""
import os
import struct

import pytest

from conformkit.aaf import AafContainer, identify_aaf
from conformkit.aafwrite import AafWriteError, available, write_binary_aaf
from conformkit.edl import parse_edl
from conformkit.timecode import rate_for

R25 = rate_for("25")
EDL_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "fixtures",
                          "test_25.edl")

needs_aaf2 = pytest.mark.skipif(not available(), reason="pyaaf2 not installed")


def sample_list():
    if not os.path.exists(EDL_SAMPLE):
        pytest.skip("EDL fixture missing")
    with open(EDL_SAMPLE, encoding="utf-8", errors="replace") as fh:
        return parse_edl(fh.read(), R25, "CMX3600", strict_roll_length=False)


def test_available_reports_status():
    assert isinstance(available(), bool)


def test_trim_ends_removed():
    """It never did anything; keeping it implied a behaviour that did not
    exist. Callers write the list exactly as given."""
    import inspect

    from conformkit.aaf import place_clips

    assert "trim_ends" not in inspect.signature(place_clips).parameters
    assert "trim_ends" not in inspect.signature(write_binary_aaf).parameters


def test_missing_dependency_message(monkeypatch):
    """Without pyaaf2 the error must tell the user what to install."""
    import conformkit.aafwrite as m

    monkeypatch.setattr(m, "available", lambda: False)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def fake_import(name, *a, **kw):
        if name == "aaf2":
            raise ImportError("no aaf2")
        return real_import(name, *a, **kw)

    monkeypatch.setattr("builtins.__import__", fake_import)
    with pytest.raises(AafWriteError, match="pyaaf2"):
        m.write_binary_aaf("x.aaf", sample_list(), R25)


@needs_aaf2
def test_writes_valid_compound_file(tmp_path):
    out = tmp_path / "out.aaf"
    summary = write_binary_aaf(str(out), sample_list(), R25, title="test")

    assert out.exists()
    assert summary["events"] > 0
    assert summary["tracks"] > 0
    assert summary["size"] == out.stat().st_size

    data = out.read_bytes()
    assert data[:8] == bytes.fromhex("D0CF11E0A1B11AE1")


@needs_aaf2
def test_output_identified_as_aaf(tmp_path):
    out = tmp_path / "out.aaf"
    write_binary_aaf(str(out), sample_list(), R25)
    info = identify_aaf(out.read_bytes())
    assert info["is_aaf"] is True
    assert info["looks_like_aaf"] is True
    assert info["streams"] > 0
    assert info["smpte_ul_count"] > 0


@needs_aaf2
def test_our_reader_parses_our_writer(tmp_path):
    """Round trip through the independent CFB implementation."""
    out = tmp_path / "out.aaf"
    write_binary_aaf(str(out), sample_list(), R25, title="roundtrip")

    container = AafContainer(str(out))
    assert len(container.directory_all()) > 10
    assert len(container.streams()) > 1
    names = container.names()
    assert names


@needs_aaf2
def test_mob_names_present(tmp_path):
    out = tmp_path / "out.aaf"
    src = sample_list()
    write_binary_aaf(str(out), src, R25)

    names = AafContainer(str(out)).names()
    # the source names written from the edit list must survive
    joined = "\n".join(names)
    expected = {e.source_file or e.clip_name for e in src if e.clip_name}
    assert any(name in joined for name in list(expected)[:5])


@needs_aaf2
def test_rejects_list_without_tracks(tmp_path):
    from conformkit.model import EditList

    empty = EditList(rate=R25)
    with pytest.raises(AafWriteError, match="no tracks"):
        write_binary_aaf(str(tmp_path / "x.aaf"), empty, R25)


@needs_aaf2
def test_rejects_list_without_rate(tmp_path):
    from conformkit.model import EditList

    with pytest.raises(AafWriteError, match="frame rate"):
        write_binary_aaf(str(tmp_path / "x.aaf"), EditList(), None)


@needs_aaf2
def test_zero_length_events_skipped(tmp_path):
    from conformkit.model import EditList

    src = sample_list()
    out = tmp_path / "z.aaf"
    summary = write_binary_aaf(str(out), src, R25)
    # the writer must not emit clips for zero-length anchors: one event per
    # non-zero event, not one per input event
    expected = len([e for e in src if e.rec_duration(R25) > 0])
    assert summary["events"] == expected
    assert summary["events"] < len(src)


@needs_aaf2
def test_rate_is_rational(tmp_path):
    out = tmp_path / "r.aaf"
    summary = write_binary_aaf(str(out), sample_list(), R25)
    assert summary["rate"] == "25/1"


@needs_aaf2
def test_drop_frame_rate_string(tmp_path):
    from conformkit.timecode import rate_for as rf

    df = rf("29.97", df=True)
    out = tmp_path / "df.aaf"
    summary = write_binary_aaf(str(out), sample_list(), df)
    num, den = summary["rate"].split("/")
    assert int(den) == 1001
    assert int(num) == 30000
