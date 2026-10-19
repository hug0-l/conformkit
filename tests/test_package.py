"""Packaging and installed-entry-point tests; no fixtures required."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from conformkit import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    assert match, "no version field in pyproject.toml"
    assert match.group(1) == __version__


def test_cli_version_flag(capsys):
    from conformkit.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"conformkit {__version__}"


def test_module_version_flag():
    result = subprocess.run(
        [sys.executable, "-m", "conformkit", "--version"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == f"conformkit {__version__}"
