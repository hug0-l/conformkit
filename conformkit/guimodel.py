"""View model for the desktop GUI.

Everything here is pure data transformation with no Tk dependency, so the
logic that decides what the window shows can be tested without a display.

The GUI itself (`conformkit.gui`) is a thin rendering layer over this.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .timecode import Rate, format_tc, rate_for

__all__ = [
    "DocumentKind", "LoadedDocument", "Column", "load_document",
    "columns_for", "rows_for", "summary_lines", "DETAIL_TABS",
]


class DocumentKind:
    EDL = "edl"
    PTX = "ptx"
    AAF = "aaf"
    WAV = "wav"
    UNKNOWN = "unknown"


@dataclass
class Column:
    key: str
    title: str
    width: int = 120
    anchor: str = "w"


@dataclass
class LoadedDocument:
    """A file opened in the GUI, normalised to one shape."""

    path: str
    kind: str = DocumentKind.UNKNOWN
    size: int = 0

    # EDL
    edit_list: object | None = None
    rate: Rate | None = None

    # PTX
    session: object | None = None

    # AAF
    container: object | None = None

    # WAV
    wav_info: object | None = None

    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def ok(self) -> bool:
        return not self.error and self.kind != DocumentKind.UNKNOWN


# --------------------------------------------------------------- loading

def _sniff(path: str) -> str:
    """Decide the kind from content first, then extension."""
    from .ptx import identify as identify_ptx

    try:
        with open(path, "rb") as fh:
            head = fh.read(65536)
    except OSError:
        return DocumentKind.UNKNOWN

    fmt = identify_ptx(head, path)
    if fmt.is_pro_tools:
        return DocumentKind.PTX

    if head[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        return DocumentKind.AAF

    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return DocumentKind.WAV

    text = head.decode("utf-8", "replace")
    if "TITLE:" in text or "FCM:" in text or text.lstrip().startswith("001"):
        return DocumentKind.EDL

    ext = os.path.splitext(path)[1].lower()
    return {
        ".edl": DocumentKind.EDL,
        ".ptx": DocumentKind.PTX,
        ".ptf": DocumentKind.PTX,
        ".pts": DocumentKind.PTX,
        ".aaf": DocumentKind.AAF,
        ".wav": DocumentKind.WAV,
    }.get(ext, DocumentKind.UNKNOWN)


def load_document(path: str, rate_label: str = "25",
                  progress=None) -> LoadedDocument:
    """Open any supported file and normalise it.

    `progress` is an optional callable receiving (fraction, message) so a GUI
    can drive a progress bar without this module knowing about Tk.
    """
    def report(frac: float, msg: str) -> None:
        if progress:
            progress(frac, msg)

    doc = LoadedDocument(path=path)
    try:
        doc.size = os.path.getsize(path)
    except OSError as exc:
        doc.error = str(exc)
        return doc

    report(0.02, "identifying")
    doc.kind = _sniff(path)

    try:
        if doc.kind == DocumentKind.EDL:
            report(0.10, "reading edit list")
            from .edl import detect_dialect, parse_edl

            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            doc.rate = rate_for(rate_label)
            dialect = detect_dialect(text)
            doc.edit_list = parse_edl(text, doc.rate, dialect,
                                      strict_roll_length=False)
            doc.warnings.extend(doc.edit_list.warnings[:50])

        elif doc.kind == DocumentKind.PTX:
            report(0.05, "decrypting session")
            from .ptx import read_session

            doc.session = read_session(path)

        elif doc.kind == DocumentKind.AAF:
            report(0.10, "reading container")
            from .aaf import AafContainer

            doc.container = AafContainer(path)

        elif doc.kind == DocumentKind.WAV:
            report(0.10, "reading WAV")
            from .bwf import read_wav_info

            doc.wav_info = read_wav_info(path=path)

        else:
            doc.error = "unrecognised file type"

    except Exception as exc:                        # noqa: BLE001 - shown to user
        doc.error = str(exc)

    report(1.0, "done")
    return doc


# --------------------------------------------------------------- columns

def columns_for(doc: LoadedDocument) -> list[Column]:
    if doc.kind == DocumentKind.EDL:
        return [
            Column("no", "#", 50, "e"),
            Column("roll", "Roll", 150),
            Column("track", "Track", 70),
            Column("trans", "Tr", 45, "center"),
            Column("src_in", "Source In", 110),
            Column("src_out", "Source Out", 110),
            Column("rec_in", "Record In", 110),
            Column("rec_out", "Record Out", 110),
            Column("clip", "Clip Name", 260),
        ]
    if doc.kind == DocumentKind.PTX:
        return [
            Column("name", "Track", 320),
            Column("index", "#", 60, "e"),
        ]
    if doc.kind == DocumentKind.AAF:
        return [
            Column("name", "Name", 300),
            Column("kind", "Kind", 90),
            Column("size", "Size", 100, "e"),
        ]
    if doc.kind == DocumentKind.WAV:
        return [
            Column("field", "Field", 200),
            Column("value", "Value", 420),
        ]
    return []


# --------------------------------------------------------------- rows

def rows_for(doc: LoadedDocument, tab: str = "main") -> list[tuple]:
    """Rows for the given tab, as tuples matching `columns_for`."""
    if not doc.ok:
        return []

    if doc.kind == DocumentKind.EDL:
        el = doc.edit_list
        rate = doc.rate
        if tab == "clips":
            return [(i, ev.clip_name or "-")
                    for i, ev in enumerate(el, start=1) if ev.clip_name]
        return [
            (
                i,
                ev.roll_name,
                "/".join(str(t) for t in ev.tracks),
                ev.transition.kind,
                format_tc(ev.src_in, rate),
                format_tc(ev.src_out, rate),
                format_tc(ev.rec_in, rate),
                format_tc(ev.rec_out, rate),
                ev.clip_name,
            )
            for i, ev in enumerate(el, start=1)
        ]

    if doc.kind == DocumentKind.PTX:
        s = doc.session
        if tab == "clips":
            return [(n, i) for i, n in enumerate(s.clip_names, start=1)]
        if tab == "plugins":
            return [(n, i) for i, n in enumerate(s.plugins, start=1)]
        if tab == "xml":
            return [(f"<{tag}>", f"0x{off:08x}", f"{len(chunk):,}")
                    for off, chunk, tag, _ in s.xml_documents]
        return [(n, i) for i, n in enumerate(s.track_names, start=1)]

    if doc.kind == DocumentKind.AAF:
        c = doc.container
        if tab == "names":
            return [(n, "", "") for n in c.names()[:20000]]
        return [(e.name, e.kind, f"{e.size:,}") for e in c.directory_all()]

    if doc.kind == DocumentKind.WAV:
        w = doc.wav_info
        return [
            ("channels", str(w.channels)),
            ("sample rate", f"{w.sample_rate} Hz"),
            ("bits", str(w.bits_per_sample)),
            ("frames", f"{w.duration_frames:,}"),
            ("duration", f"{w.sample_duration:.3f} s"),
            ("bext originator", w.bext_originator or "-"),
            ("bext date", w.bext_date or "-"),
            ("bext time reference", f"{w.bext_time_reference:,}"),
            ("iXML project", w.ix_project or "-"),
            ("iXML scene", w.ix_scene or "-"),
            ("iXML take", w.ix_take or "-"),
            ("iXML tape", w.ix_tape or "-"),
        ]
    return []


DETAIL_TABS = {
    DocumentKind.EDL: [("main", "Events"), ("clips", "Clips")],
    DocumentKind.PTX: [("main", "Tracks"), ("clips", "Clips"),
                       ("plugins", "Plug-ins"), ("xml", "XML")],
    DocumentKind.AAF: [("main", "Streams"), ("names", "Names")],
    DocumentKind.WAV: [("main", "Metadata")],
    DocumentKind.UNKNOWN: [],
}


# --------------------------------------------------------------- summary

def summary_lines(doc: LoadedDocument) -> list[tuple[str, str]]:
    """Label/value pairs for the info panel."""
    if doc.error:
        return [("error", doc.error)]

    if doc.kind == DocumentKind.EDL:
        el = doc.edit_list
        rows = [
            ("file", doc.name),
            ("kind", "EDL"),
            ("size", f"{doc.size:,} bytes"),
            ("rate", f"{doc.rate.label}{' DF' if doc.rate.df else ''}"),
            ("title", el.title or "-"),
            ("events", f"{len(el):,}"),
        ]
        rolls = {e.roll_name for e in el}
        tracks = {t.label for e in el for t in e.tracks}
        rows.append(("rolls", f"{len(rolls):,}"))
        rows.append(("tracks", ", ".join(sorted(tracks)) or "-"))
        if el.warnings:
            rows.append(("warnings", f"{len(el.warnings):,}"))
        return rows

    if doc.kind == DocumentKind.PTX:
        s = doc.session
        return [
            ("file", doc.name),
            ("kind", f"Pro Tools {s.fmt.kind}"),
            ("PRONOM", s.fmt.pronom),
            ("size", f"{doc.size:,} bytes"),
            ("session", s.session_name or "-"),
            ("user", s.user_name or "-"),
            ("host", s.computer_name or "-"),
            ("sample rate", f"{s.sample_rate:,} Hz" if s.sample_rate else "-"),
            ("encryption", f"type 0x{s.xor_type:02x} / value 0x{s.xor_value:02x}"),
            ("blocks", f"{s.block_count:,}"),
            ("strings", f"{s.string_count:,}"),
            ("XML docs", f"{len(s.xml_documents):,}"),
            ("tracks", f"{len(s.track_names):,}"),
            ("clips", f"{len(s.clip_names):,}"),
            ("plug-ins", f"{len(s.plugins):,}"),
        ]

    if doc.kind == DocumentKind.AAF:
        info = doc.container.info()
        return [
            ("file", doc.name),
            ("kind", "AAF (binary)"),
            ("size", f"{doc.size:,} bytes"),
            ("SDK version", info.get("aaf_version", "-")),
            ("streams", str(info.get("streams", "-"))),
            ("SMPTE keys", f"{info.get('smpte_ul_count', 0):,}"),
            ("sector size", str(info.get("sector_size", "-"))),
        ]

    if doc.kind == DocumentKind.WAV:
        w = doc.wav_info
        return [
            ("file", doc.name),
            ("kind", "WAV / BWF"),
            ("size", f"{doc.size:,} bytes"),
            ("channels", str(w.channels)),
            ("sample rate", f"{w.sample_rate} Hz"),
            ("duration", f"{w.sample_duration:.3f} s"),
        ]

    return [("file", doc.name), ("kind", "unknown")]
