"""Shooting-script import: extract scene numbers and descriptions.

The reference product reads PDF and plain text, with layout heuristics for
where the scene number sits. Text parsing is implemented here; PDF extraction
is delegated to an optional dependency so the core stays dependency-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Marker
from .timecode import Rate, Timecode, add

__all__ = ["ScriptError", "ScriptLine", "parse_script_text", "script_to_markers",
           "extract_pdf_text"]


class ScriptError(ValueError):
    pass


@dataclass
class ScriptLine:
    scene: str = ""
    description: str = ""
    page: int = 0
    raw: str = ""


# "1   INT. KITCHEN - DAY   1"
# "12A  EXT. STREET - NIGHT"
# "1A.  INT. CAR - CONTINUOUS"
_SCENE_RE = re.compile(
    r"^\s*(?P<scene>\d{1,4}[A-Z]{0,3})[\.\s]\s*"
    r"(?P<desc>(?:INT|EXT|I/E|INT\./EXT\.)[^\r\n]{0,200})",
    re.IGNORECASE,
)

# scene number on the right-hand side: "INT. KITCHEN - DAY        1"
_SCENE_RIGHT_RE = re.compile(
    r"^\s*(?P<desc>(?:INT|EXT|I/E|INT\./EXT\.)[^\r\n]{3,200}?)\s{2,}"
    r"(?P<scene>\d{1,4}[A-Z]{0,3})\s*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_script_text(text: str, start_page: int = 1) -> list[ScriptLine]:
    """Extract scene headings from plain-text or extracted-PDF script text.

    Handles both layouts: scene number on the left, and scene number pushed to
    the right margin. Never raises on unrecognised content; returns only what
    it can identify.
    """
    if not text:
        return []

    # reject content that is mostly control bytes
    printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t")
    if printable / max(len(text), 1) < 0.8:
        return []

    out: list[ScriptLine] = []
    page = start_page

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        # form feed marks a page break in PDF text extraction
        if "\f" in line:
            page += line.count("\f")
            line = line.replace("\f", " ")

        m = _SCENE_RE.match(line)
        if m:
            out.append(ScriptLine(
                scene=m.group("scene").upper(),
                description=_clean(m.group("desc")),
                page=page, raw=line,
            ))
            continue

        m2 = _SCENE_RIGHT_RE.match(line)
        if m2:
            out.append(ScriptLine(
                scene=m2.group("scene").upper(),
                description=_clean(m2.group("desc")),
                page=page, raw=line,
            ))

    return out


def script_to_markers(lines: list[ScriptLine], rate: Rate,
                      prefix: str = "SC ",
                      comment_template: str = "{description}",
                      spacing_frames: int = 0) -> list[Marker]:
    """Turn parsed script lines into timeline markers.

    Markers are laid out sequentially starting at 01:00:00:00 with an optional
    fixed spacing. A real workflow would align them to picture; this provides
    the deterministic baseline the product uses before the user adjusts.
    """
    markers: list[Marker] = []
    cursor = Timecode(1, 0, 0, 0)
    for line in lines:
        if not line.scene:
            continue
        markers.append(Marker(
            position=cursor,
            name=f"{prefix}{line.scene}".strip(),
            comment=comment_template.format(
                description=line.description, scene=line.scene),
        ))
        if spacing_frames:
            cursor = add(cursor, spacing_frames, rate)
    return markers


def extract_pdf_text(path: str, password: str | None = None) -> str:
    """Extract text from a PDF.

    Requires pypdf; raises ScriptError with a clear message when unavailable so
    the caller can fall back to text import.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:                      # pragma: no cover
        raise ScriptError(
            "PDF import needs the 'pypdf' package; "
            "install it or import a plain-text script instead") from exc

    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            if password is None:
                raise ScriptError("PDF is password protected")
            if not reader.decrypt(password):
                raise ScriptError("PDF password rejected")
        chunks = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
            chunks.append("\f")
        return "\n".join(chunks)
    except ScriptError:
        raise
    except Exception as exc:                        # noqa: BLE001
        raise ScriptError(f"could not read PDF: {exc}") from exc
