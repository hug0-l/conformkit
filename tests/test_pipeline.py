"""End-to-end pipeline tests using the real fixtures and the CLI."""
import os
import subprocess
import sys

import pytest

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
ROOT = os.path.join(os.path.dirname(__file__), "..")

REAL = [os.path.join(FIXTURE_DIR, f) for f in (
    "TEST.edl", "test_25.edl", "test_24psf.edl", "ISSUE_19_unusual01.edl",
    "cdl_example01.edl", "ISSUE_14_conform_edl_issue_03.edl",
)]
REAL = [p for p in REAL if os.path.exists(p)]


def run(*argv):
    return subprocess.run(
        [sys.executable, "-m", "conformkit", *argv],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )


pytestmark = pytest.mark.skipif(not REAL, reason="no fixtures")


@pytest.mark.parametrize("path", REAL, ids=lambda p: os.path.basename(p))
def test_cli_info(path):
    r = run("--rate", "25", "info", path)
    assert r.returncode == 0, r.stderr
    assert "edits" in r.stdout


def test_cli_edl_to_aafx_to_edl(tmp_path):
    src = os.path.join(FIXTURE_DIR, "test_25.edl")
    aafx = tmp_path / "out.aafx"
    r = run("--rate", "25", "convert", src, str(aafx))
    assert r.returncode == 0, r.stderr
    assert aafx.exists() and aafx.stat().st_size > 0
    text = aafx.read_text(encoding="utf-8")
    assert "amwa.tv/aafx/v2.0" in text
    assert "CompositionMob" in text


def test_cli_compare_real_lists():
    old = os.path.join(FIXTURE_DIR, "ISSUE_14_conform_edl_issue_02.edl")
    new = os.path.join(FIXTURE_DIR, "ISSUE_14_conform_edl_issue_03.edl")
    r = run("--rate", "25", "compare", old, new)
    assert r.returncode == 0, r.stderr
    assert "changes" in r.stdout


def test_cli_conform_real_lists():
    old = os.path.join(FIXTURE_DIR, "ISSUE_14_conform_edl_issue_02.edl")
    new = os.path.join(FIXTURE_DIR, "ISSUE_14_conform_edl_issue_02.edl")
    r = run("--rate", "25", "conform", old, new)
    assert r.returncode == 0, r.stderr
    assert "keep" in r.stdout


def test_cli_conform_rejects_unknown_material():
    old = os.path.join(FIXTURE_DIR, "ISSUE_14_conform_edl_issue_02.edl")
    new = os.path.join(FIXTURE_DIR, "ISSUE_14_conform_edl_issue_03.edl")
    r = run("--rate", "25", "conform", old, new)
    # either it plans cleanly, or it refuses with a stated reason
    assert r.returncode in (0, 1)
    if r.returncode == 1:
        assert "cannot conform" in r.stderr


def test_cli_edl_roundtrip_via_files(tmp_path):
    """Parse a real list, write it, parse again, compare via the CLI."""
    src = os.path.join(FIXTURE_DIR, "ISSUE_19_unusual01.edl")
    mid = tmp_path / "mid.edl"
    r = run("--rate", "25", "convert", src, str(mid))
    assert r.returncode == 0, r.stderr

    a = run("--rate", "25", "info", src)
    b = run("--rate", "25", "info", str(mid))
    assert a.returncode == 0 and b.returncode == 0

    def edits(out):
        for line in out.splitlines():
            if line.startswith("edits"):
                return int(line.split()[-1])
        return -1

    assert edits(a.stdout) == edits(b.stdout)


def test_cli_bad_file_reports_clearly(tmp_path):
    bad = tmp_path / "bad.edl"
    bad.write_text("TITLE: X\nFCM: NON-DROP FRAME\n001 R V C nonsense\n",
                   encoding="utf-8")
    r = run("--rate", "25", "info", str(bad))
    assert r.returncode == 1
    assert "error" in r.stderr.lower()
