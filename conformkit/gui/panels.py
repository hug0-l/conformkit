"""Document panels: one per file kind.

A panel is the whole answer to "how does this kind of document appear in the
list". Adding support for a new format means writing one panel and registering
it — the application shell does not change.

Contract:

    kind        str            matches guimodel.DocumentKind
    title(doc)  str            heading line
    columns(doc) list[Column]  column definitions
    rows(doc)   list[tuple]    row values, in column order
    filters(doc) list[str]     values for the track filter
    counts(doc, total, shown, selected) -> str

`Column` carries the display rules: width, anchor, and whether the user may
hide it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

__all__ = [
    "Column", "Filter", "DocumentPanel", "PanelRegistry", "PANELS",
    "register_panel", "panel_for", "EdlPanel", "PtxPanel", "AafPanel",
    "WavPanel",
]


@dataclass(frozen=True)
class Column:
    key: str
    title: str
    width: int = 100
    anchor: str = "w"
    always: bool = False          # cannot be hidden
    visible: bool = True          # default visibility
    stretch: bool = False


@dataclass(frozen=True)
class Filter:
    key: str
    label: str
    values: list[str] = field(default_factory=list)   # empty = free text
    width: int = 14


class DocumentPanel(Protocol):
    kind: str

    def title(self, doc) -> str: ...
    def columns(self, doc) -> list[Column]: ...
    def rows(self, doc) -> list[tuple]: ...
    def filters(self, doc) -> list[Filter]: ...
    def counts(self, doc, total: int, shown: int, selected: int) -> str: ...
    def match(self, doc, row: tuple, filters: dict[str, str]) -> bool: ...


class PanelRegistry:
    """Maps a document kind to its panel.

    Registration is explicit and ordered, so the UI can also list which
    formats it supports.
    """

    def __init__(self) -> None:
        self._panels: dict[str, DocumentPanel] = {}

    def register(self, panel: DocumentPanel) -> DocumentPanel:
        self._panels[panel.kind] = panel
        return panel

    def get(self, kind: str) -> DocumentPanel | None:
        return self._panels.get(kind)

    def kinds(self) -> list[str]:
        return list(self._panels)

    def __contains__(self, kind: str) -> bool:
        return kind in self._panels


PANELS = PanelRegistry()


def register_panel(panel: DocumentPanel) -> DocumentPanel:
    return PANELS.register(panel)


def panel_for(kind: str) -> DocumentPanel | None:
    return PANELS.get(kind)


def _default_counts(doc, total: int, shown: int, selected: int) -> str:
    parts = [f"Events {total:,}"]
    if shown != total:
        parts.append(f"Shown {shown:,}")
    if selected:
        parts.append(f"Selected {selected:,}")
    parts.append(doc.kind.upper())
    return "     ".join(parts)


# ---------------------------------------------------------------- EDL

class EdlPanel:
    """Edit lists: the primary view, and the one the columns are named for."""

    kind = "edl"

    def title(self, doc) -> str:
        import os

        el = doc.edit_list
        name = (el.title or "").strip() or \
            os.path.splitext(os.path.basename(doc.path))[0] or "Untitled"
        if el.warnings:
            name += f"   ({len(el.warnings)} warning(s))"
        return name

    def columns(self, doc) -> list[Column]:
        return [
            Column("no",       "EvNo",            54,  "e",  always=True),
            Column("flag",     "+",               26,  "center", always=True),
            Column("roll",     "RollName",        132, "w",  always=True),
            Column("src_in",   "Source In",       102, "w",  always=True),
            Column("src_out",  "Source Out",      102, "w",  always=True),
            Column("rec_in",   "Destination In",  110, "w",  always=True),
            Column("rec_out",  "Destination Out", 116, "w",  always=True),
            Column("track",    "Trk",             52,  "center"),
            Column("trans",    "Tr",              38,  "center"),
            Column("clip",     "Clip Name",       220, "w",  stretch=True),
            Column("bad_speed", "Bad Speed",      76,  "w"),
            Column("vfx",      "VFX Update",      84,  "w",  visible=False),
            Column("comments", "Comments",        190, "w",  visible=False,
                   stretch=True),
            Column("srce_fc",  "Srce FC",         68,  "e",  visible=False),
            Column("dest_durn", "Dest Durn",      74,  "e",  visible=False),
            Column("offset",   "Offset",          62,  "e",  visible=False),
            Column("speed",    "Speed",           56,  "e",  visible=False),
            Column("type",     "Type",            58,  "w",  visible=False),
        ]

    def rows(self, doc) -> list[tuple]:
        from ..timecode import format_tc

        rate = doc.rate
        out = []
        for i, ev in enumerate(doc.edit_list, start=1):
            rec_dur = ev.rec_duration(rate)
            src_dur = ev.src_duration(rate)
            bad = src_dur != rec_dur
            speed = f"{src_dur / rec_dur:.4f}" if (rec_dur and bad) else ""
            out.append((
                f"{i:03d}",
                "+" if (ev.clip_name or ev.comments) else "",
                ev.roll_name,
                format_tc(ev.src_in, rate),
                format_tc(ev.src_out, rate),
                format_tc(ev.rec_in, rate),
                format_tc(ev.rec_out, rate),
                "/".join(str(t) for t in ev.tracks),
                ev.transition.kind,
                ev.clip_name,
                "!" if bad else "",
                ev.vfx_update,
                "; ".join(ev.comments),
                str(src_dur),
                str(rec_dur),
                "",
                speed,
                ev.transition.name,
            ))
        return out

    def filters(self, doc) -> list[Filter]:
        tracks = sorted({t.label for e in doc.edit_list for t in e.tracks})
        return [
            Filter("track", "Track", ["All", *tracks], 9),
            Filter("roll", "Roll", [], 14),
            Filter("search", "Search", [], 22),
        ]

    def counts(self, doc, total: int, shown: int, selected: int) -> str:
        rolls = {e.roll_name for e in doc.edit_list}
        parts = [f"Events {total:,}"]
        if shown != total:
            parts.append(f"Shown {shown:,}")
        if selected:
            parts.append(f"Selected {selected:,}")
        parts.append(f"Rolls {len(rolls):,}")
        parts.append(f"Frame Count {doc.rate.label}")
        return "     ".join(parts)

    def match(self, doc, row: tuple, filters: dict[str, str]) -> bool:
        track = filters.get("track", "All")
        roll = filters.get("roll", "").strip().casefold()
        search = filters.get("search", "").strip().casefold()

        if track and track != "All" and track not in str(row[7]).split("/"):
            return False
        if roll and roll not in str(row[2]).casefold():
            return False
        if search and not any(search in str(v).casefold() for v in row):
            return False
        return True


# ---------------------------------------------------------------- PTX

class PtxPanel:
    kind = "ptx"

    def title(self, doc) -> str:
        s = doc.session
        return s.session_name or "Pro Tools session"

    def columns(self, doc) -> list[Column]:
        return [
            Column("no",   "EvNo",  54, "e", always=True),
            Column("name", "Track", 340, "w", always=True, stretch=True),
            Column("kind", "Kind",  90, "w"),
        ]

    def rows(self, doc) -> list[tuple]:
        return [(f"{i:03d}", name, "track")
                for i, name in enumerate(doc.session.track_names, start=1)]

    def filters(self, doc) -> list[Filter]:
        return [Filter("search", "Search", [], 22)]

    def counts(self, doc, total: int, shown: int, selected: int) -> str:
        s = doc.session
        parts = [f"Tracks {total:,}"]
        if shown != total:
            parts.append(f"Shown {shown:,}")
        parts.append(f"Clips {len(s.clip_names):,}")
        parts.append(f"Blocks {s.block_count:,}")
        return "     ".join(parts)

    def match(self, doc, row: tuple, filters: dict[str, str]) -> bool:
        search = filters.get("search", "").strip().casefold()
        return not search or any(search in str(v).casefold() for v in row)


# ---------------------------------------------------------------- AAF

class AafPanel:
    kind = "aaf"

    def title(self, doc) -> str:
        return doc.name or "AAF"

    def columns(self, doc) -> list[Column]:
        return [
            Column("no",   "EvNo", 54,  "e", always=True),
            Column("name", "Name", 320, "w", always=True, stretch=True),
            Column("kind", "Kind", 90,  "w"),
            Column("size", "Size", 100, "e"),
        ]

    def rows(self, doc) -> list[tuple]:
        return [(f"{i:03d}", e.name, e.kind, f"{e.size:,}")
                for i, e in enumerate(doc.container.directory_all(), start=1)]

    def filters(self, doc) -> list[Filter]:
        return [Filter("search", "Search", [], 22)]

    def counts(self, doc, total: int, shown: int, selected: int) -> str:
        info = doc.container.info()
        parts = [f"Entries {total:,}"]
        if shown != total:
            parts.append(f"Shown {shown:,}")
        parts.append(f"SMPTE keys {info.get('smpte_ul_count', 0):,}")
        return "     ".join(parts)

    def match(self, doc, row: tuple, filters: dict[str, str]) -> bool:
        search = filters.get("search", "").strip().casefold()
        return not search or any(search in str(v).casefold() for v in row)


# ---------------------------------------------------------------- WAV

class WavPanel:
    kind = "wav"

    def title(self, doc) -> str:
        return doc.name or "WAV"

    def columns(self, doc) -> list[Column]:
        return [
            Column("no",    "EvNo",  54,  "e", always=True),
            Column("field", "Field", 200, "w", always=True),
            Column("value", "Value", 420, "w", always=True, stretch=True),
        ]

    def rows(self, doc) -> list[tuple]:
        w = doc.wav_info
        pairs = [
            ("channels", str(w.channels)),
            ("sample rate", f"{w.sample_rate} Hz"),
            ("bit depth", str(w.bits_per_sample)),
            ("duration", f"{w.sample_duration:.3f} s"),
            ("bext originator", w.bext_originator or "-"),
            ("bext date", w.bext_date or "-"),
            ("bext time reference", f"{w.bext_time_reference:,}"),
            ("iXML project", w.ix_project or "-"),
            ("iXML scene", w.ix_scene or "-"),
            ("iXML take", w.ix_take or "-"),
            ("iXML tape", w.ix_tape or "-"),
        ]
        return [(f"{i:03d}", k, v) for i, (k, v) in enumerate(pairs, start=1)]

    def filters(self, doc) -> list[Filter]:
        return []

    def counts(self, doc, total: int, shown: int, selected: int) -> str:
        return f"Fields {total}"

    def match(self, doc, row: tuple, filters: dict[str, str]) -> bool:
        return True


# built-in registrations
register_panel(EdlPanel())
register_panel(PtxPanel())
register_panel(AafPanel())
register_panel(WavPanel())
