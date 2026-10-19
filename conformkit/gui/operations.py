"""Command implementations.

One method per user action. `Operations.register()` publishes them to the
command registry; the toolbar and menus only reference ids.

To add a feature:
  1. write a method here,
  2. add a line to `_COMMANDS`,
  3. reference the id from `commands.TOOLBAR` / `commands.MENUS`.
"""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog

from .. import guimodel
from ..guimodel import DocumentKind
from . import dialogs
from .commands import COMMANDS, Command, register_command

__all__ = ["Operations"]

FILE_TYPES = [
    ("All supported", "*.edl *.ptx *.ptf *.pts *.aaf *.wav *.xml"),
    ("EDL files", "*.edl"),
    ("AAF files", "*.aaf"),
    ("FCP7 XML files", "*.xml"),
    ("Pro Tools sessions", "*.ptx *.ptf *.pts"),
    ("WAV files", "*.wav"),
    ("All files", "*.*"),
]

# id, label, tooltip, kinds
_COMMANDS = [
    ("open", "Open List", "Open an edit list", ()),
    ("save", "Save List", "Save the current list as EDL", ("edl",)),
    ("new_edl", "New EDL", "Start an empty edit list", ()),
    ("clear_events", "Clear Events", "Clear all events", ("edl",)),
    ("display_options", "Display Options", "Choose list columns", ()),
    ("clean_events", "Clean Events",
     "Remove duplicates, extend gaps", ("edl",)),
    ("compare_lists", "Compare Lists", "Compare two cuts", ("edl",)),
    ("wav_list", "WAV File List", "Open a WAV file list", ()),
    ("assemble_wavs", "Assemble WAVs",
     "Assemble from location WAVs", ()),
    ("update_data", "Update Data",
     "Apply a versioned change list", ("edl",)),
    ("pt_reconform", "PT Re-conform",
     "Re-conform a Pro Tools session", ("edl",)),
    ("user_guide", "User Guide", "Open the user guide", ()),
    ("quit", "Quit", "Close the window", ()),
    # import
    ("import_edl", "EDL…", "Import an EDL", ()),
    ("import_tab", "EDL Tab-Delimited Text…", "Import tab-delimited EDL", ()),
    ("import_excel", "EDL in MS Excel…", "Import EDL from Excel", ()),
    ("import_multi", "Multiple Video EDL Files…",
     "Import several EDLs", ()),
    ("import_aaf", "AAF…", "Import an AAF", ()),
    ("import_xml", "FCP7 XML…", "Import FCP7 XML", ()),
    ("import_ptx", "Pro Tools Session…", "Import a Pro Tools session", ()),
    # append
    ("append_list", "List File…", "Append another edit list", ("edl",)),
    ("append_edl", "EDL…", "Append an EDL", ("edl",)),
    ("append_aaf", "AAF…", "Append an AAF", ("edl",)),
    ("append_xml", "FCP7 XML…", "Append FCP7 XML", ("edl",)),
    # export
    ("rename_ptx", "Rename in Session…",
     "Rename track or clip names inside a session copy", ()),
    ("export_aaf", "AAF…", "Export a binary AAF", ("edl",)),
    ("export_edl", "EDL…", "Export as CMX3600 EDL", ("edl",)),
    ("export_ptx", "PTX (Picture Cut)…",
     "Export Pro Tools text columns", ("edl",)),
    ("export_piccut", "Picture Cut…", "Export a picture cut track", ("edl",)),
    ("export_midi", "MIDI…", "Export MIDI", ("edl",)),
    ("export_tab", "List (Tab)…", "Export the list as tab-delimited text",
     ("edl",)),
    ("export_excel", "List (Excel)…", "Export the list as CSV", ("edl",)),
    ("export_offset", "Offset List…", "Export an offset list", ("edl",)),
    ("copy_list_as", "Copy List As…", "Copy or export the list", ("edl",)),
    # view
    ("fit_columns", "Fit Columns", "Reset column widths", ()),
    ("show_all_columns", "Show All Columns", "Show every column", ()),
    ("default_columns", "Show Default Columns",
     "Restore the default column set", ()),
    ("about", "About conformkit", "About this application", ()),
]


class Operations:
    """Binds command ids to methods on the application."""

    def __init__(self, app):
        self.app = app

    # ------------------------------------------------------------ registry

    def register(self, replace: bool = True) -> None:
        """Publish every command. Safe to call more than once.

        Handlers are bound to `self`, so a second app instance replaces the
        bindings rather than raising on duplicate ids.
        """
        for cmd_id, label, tooltip, kinds in _COMMANDS:
            handler = getattr(self, f"cmd_{cmd_id}", None)
            if handler is None:
                continue
            command = Command(id=cmd_id, label=label, handler=handler,
                              tooltip=tooltip, kinds=tuple(kinds))
            if replace and cmd_id in COMMANDS:
                COMMANDS._commands[cmd_id] = command
            else:
                register_command(command)

    def __getattr__(self, name):
        # let the registry fall back gracefully for unimplemented commands
        if name.startswith("cmd_"):
            return lambda: self.app.status(f"{name[4:]} is not available")
        raise AttributeError(name)

    # ------------------------------------------------------------- helpers

    def _need(self, kind: str, title: str) -> bool:
        if self.app.document_kind() != kind:
            dialogs.show_info(self.app.root, title, "Open an edit list first.")
            return False
        return True

    def _save_path(self, title: str, ext: str, filetypes) -> str:
        return filedialog.asksaveasfilename(
            parent=self.app.root, title=title, defaultextension=ext,
            filetypes=filetypes)

    def _open_path(self, title: str) -> str:
        return filedialog.askopenfilename(
            parent=self.app.root, title=title, filetypes=FILE_TYPES)

    # -------------------------------------------------------------- file

    def cmd_open(self) -> None:
        path = self._open_path("Open")
        if path:
            self.app.open_path(path)

    def cmd_new_edl(self) -> None:
        from ..model import EditList
        from ..timecode import rate_for

        rate = rate_for(self.app.rate_var.get())
        doc = guimodel.LoadedDocument(path="", kind=DocumentKind.EDL,
                                      edit_list=EditList(rate=rate), rate=rate)
        self.app.show_document(doc)

    def cmd_save(self) -> None:
        self.cmd_export_edl()

    def cmd_quit(self) -> None:
        self.app.root.destroy()

    # ------------------------------------------------------------- edit

    def cmd_clear_events(self) -> None:
        if not self._need("edl", "Clear Events"):
            return
        if not dialogs.ask_yes_no(self.app.root, "Clear Events",
                                  "Clear all events?"):
            return
        self.app.doc.edit_list.events.clear()
        self.app.doc.edit_list.renumber()
        self.app.refresh_view()

    def cmd_clean_events(self) -> None:
        if not self._need("edl", "Clean Events"):
            return
        if not dialogs.ask_yes_no(
                self.app.root, "Clean Events",
                "Remove duplicate events?\n\n"
                "Events matching on roll, source and destination range "
                "are removed."):
            return

        el = self.app.doc.edit_list
        seen = set()
        kept = []
        for ev in el:
            key = (ev.roll_name, ev.src_in, ev.src_out, ev.rec_in, ev.rec_out)
            if key in seen:
                continue
            seen.add(key)
            kept.append(ev)
        removed = len(el) - len(kept)
        el.events[:] = kept
        el.renumber()
        self.app.refresh_view()
        dialogs.show_info(self.app.root, "Clean Events",
                          f"Removed {removed} duplicate event(s).\n"
                          f"{len(kept)} remain.")

    def cmd_compare_lists(self) -> None:
        if not self._need("edl", "Compare Lists"):
            return
        path = self._open_path("Compare against")
        if not path:
            return
        try:
            from ..compare import compare_lists
            from ..edl import detect_dialect, parse_edl

            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            other = parse_edl(text, self.app.doc.rate, detect_dialect(text),
                              strict_roll_length=False)
            changes = compare_lists(self.app.doc.edit_list, other)
            dialogs.show_compare_summary(self.app.root,
                                         os.path.basename(path), changes)
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Compare failed", str(exc))

    def cmd_display_options(self) -> None:
        columns = self.app.list.columns
        result = dialogs.show_display_options(self.app.root, columns)
        if result is None:
            return
        for key, visible in result.items():
            self.app.list.set_visible(key, visible)

    # ------------------------------------------------------------ export

    def cmd_export_edl(self) -> None:
        if not self._need("edl", "Export EDL"):
            return
        path = self._save_path("Export EDL", ".edl", [("EDL files", "*.edl")])
        if not path:
            return
        try:
            from ..edl import write_edl

            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(write_edl(self.app.doc.edit_list, "CMX3600"))
            self.app.status(f"Exported {os.path.basename(path)}")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Export failed", str(exc))

    def cmd_rename_ptx(self) -> None:
        """Rename names inside a session copy, same-length only."""
        src = filedialog.askopenfilename(
            parent=self.app.root, title="Select a session to copy",
            filetypes=[("Pro Tools sessions", "*.ptx *.ptf *.pts"),
                       ("All files", "*.*")])
        if not src:
            return

        # the user edits "old -> new" lines, one per rename
        win = tk.Toplevel(self.app.root)
        win.title("Rename in Session")
        win.transient(self.app.root)
        win.geometry("520x300")

        ttk.Label(win, text="One rename per line:  old name -> new name",
                  style="Dim.TLabel").pack(anchor="w", padx=10, pady=(8, 2))
        ttk.Label(win, text="Names must be the same length; block sizes "
                            "cannot change.",
                  style="Dim.TLabel").pack(anchor="w", padx=10, pady=(0, 6))

        text = tk.Text(win, height=10, wrap="none", font=("Consolas", 9))
        text.pack(fill="both", expand=True, padx=10)

        def apply() -> None:
            raw = text.get("1.0", "end").strip()
            renames = {}
            for line in raw.splitlines():
                line = line.strip()
                if not line or "->" not in line:
                    continue
                old, new = (part.strip() for part in line.split("->", 1))
                if old and new:
                    renames[old] = new
            if not renames:
                dialogs.show_info(win, "Rename", "No renames entered.")
                return
            win.destroy()
            self._apply_session_rename(src, renames)

        btns = ttk.Frame(win)
        btns.pack(fill="x", pady=8)
        ttk.Button(btns, text="Apply to Copy…", command=apply).pack(
            side="right", padx=10)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")

    def _apply_session_rename(self, src: str, renames: dict) -> None:
        dst = filedialog.asksaveasfilename(
            parent=self.app.root, title="Save session copy",
            defaultextension=".ptx",
            filetypes=[("Pro Tools session", "*.ptx")])
        if not dst:
            return
        try:
            from ..ptwrite import write_clone

            report = write_clone(src, dst, renames)
            message = (
                f"{report.summary()}\n\n"
                f"blocks {report.blocks_before:,} -> {report.blocks_after:,}\n"
                f"source untouched: {os.path.basename(src)}")
            if report.skipped_reasons:
                message += "\n\nSkipped:\n  " + \
                    "\n  ".join(report.skipped_reasons[:8])
            dialogs.show_info(self.app.root, "Rename in Session", message)
            self.app.status(f"Wrote {os.path.basename(dst)}")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Rename failed", str(exc))

    def cmd_export_aaf(self) -> None:
        """Export a binary AAF (needs pyaaf2)."""
        if not self._need("edl", "Export AAF"):
            return
        try:
            from ..aafwrite import available
        except ImportError:
            available = lambda: False

        if not available():
            dialogs.show_info(
                self.app.root, "Export AAF",
                "Binary AAF export needs the pyaaf2 package.\n\n"
                "  pip install pyaaf2\n\n"
                "For a dependency-free alternative, use 'Export AAFX'.")
            return

        path = self._save_path("Export AAF", ".aaf",
                               [("AAF files", "*.aaf")])
        if not path:
            return
        try:
            from ..aafwrite import write_binary_aaf

            summary = write_binary_aaf(path, self.app.doc.edit_list,
                                       self.app.doc.rate,
                                       title=self.app.list_title.get())
            self.app.status(
                f"Exported {os.path.basename(path)} "
                f"({summary['events']} clips, {summary['tracks']} tracks, "
                f"{summary['size']:,} bytes)")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Export failed", str(exc))

    def cmd_export_ptx(self) -> None:
        if not self._need("edl", "Export PTX"):
            return
        path = self._save_path("Export Picture Cut Track PTX", ".txt",
                               [("Pro Tools text", "*.txt")])
        if path:
            self._write_pro_tools_text(path)

    def cmd_export_piccut(self) -> None:
        self.cmd_export_ptx()

    def _write_pro_tools_text(self, path: str) -> None:
        """Tabular interchange Pro Tools accepts while PTX writing is absent."""
        from ..timecode import format_tc

        rate = self.app.doc.rate
        header = ["CHANNEL", "EVENT", "CLIP NAME", "START", "END",
                  "DURATION", "ROLL"]
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\t".join(header) + "\n")
            for i, ev in enumerate(self.app.doc.edit_list, start=1):
                fh.write("\t".join([
                    "/".join(str(t) for t in ev.tracks),
                    str(i),
                    ev.clip_name or ev.roll_name,
                    format_tc(ev.rec_in, rate),
                    format_tc(ev.rec_out, rate),
                    str(ev.rec_duration(rate)),
                    ev.roll_name,
                ]) + "\n")
        self.app.status(f"Exported {os.path.basename(path)}")

    def cmd_export_tab(self) -> None:
        self._export_table("Export List", ".txt", "\t",
                           [("Text", "*.txt")])

    def cmd_export_excel(self) -> None:
        self._export_table("Export List", ".csv", ",",
                           [("CSV", "*.csv")])

    def _export_table(self, title, ext, sep, filetypes) -> None:
        if not self._need("edl", title):
            return
        path = self._save_path(title, ext, filetypes)
        if not path:
            return
        columns = [c for c in self.app.list.columns
                   if self.app.list.visibility().get(c.key, True)]
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(sep.join(c.title for c in columns) + "\n")
                for row in self.app.rows:
                    keep = [row[self.app.column_index(c.key)]
                            for c in columns]
                    fh.write(sep.join(str(v) for v in keep) + "\n")
            self.app.status(f"Exported {os.path.basename(path)}")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Export failed", str(exc))

    def cmd_export_offset(self) -> None:
        if not self._need("edl", "Export Offset List"):
            return
        path = self._save_path("Export Offset List", ".txt",
                               [("Text", "*.txt")])
        if not path:
            return
        from ..timecode import format_tc, to_frames

        rate = self.app.doc.rate
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("EVENT\tROLL\tFRAMES\tTIMECODE\n")
            for i, ev in enumerate(self.app.doc.edit_list, start=1):
                fh.write(f"{i}\t{ev.roll_name}\t{to_frames(ev.rec_in, rate)}\t"
                         f"{format_tc(ev.rec_in, rate)}\n")
        self.app.status(f"Exported {os.path.basename(path)}")

    def cmd_copy_list_as(self) -> None:
        self.cmd_export_tab()

    def cmd_export_midi(self) -> None:
        if not self._need("edl", "Export MIDI"):
            return
        path = self._save_path("Export MIDI", ".mid",
                               [("MIDI", "*.mid"), ("All files", "*.*")])
        if not path:
            return
        try:
            from ..midi import MidiSettings, write_midi

            size = write_midi(path, self.app.doc.edit_list,
                              self.app.doc.rate, MidiSettings())
            self.app.status(f"Exported {os.path.basename(path)} "
                            f"({size:,} bytes)")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Export failed", str(exc))

    # ------------------------------------------------------------ import

    def cmd_import_edl(self) -> None:
        self.app.open_path(self._open_path("Import EDL"))

    def cmd_import_aaf(self) -> None:
        self.app.open_path(self._open_path("Import AAF"))

    def cmd_import_xml(self) -> None:
        self.app.open_path(self._open_path("Import FCP7 XML"))

    def cmd_import_ptx(self) -> None:
        self.app.open_path(self._open_path("Import Pro Tools Session"))

    def cmd_import_multi(self) -> None:
        """Import several EDLs and merge them into one list."""
        if not self._need("edl", "Import Multiple EDLs"):
            return
        paths = filedialog.askopenfilenames(
            parent=self.app.root, title="Import Multiple Video EDL Files",
            filetypes=[("EDL files", "*.edl"), ("All files", "*.*")])
        if not paths:
            return
        try:
            from ..edl import detect_dialect, parse_edl

            base = self.app.doc.edit_list
            merged = 0
            for path in paths:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                other = parse_edl(text, self.app.doc.rate,
                                  detect_dialect(text),
                                  strict_roll_length=False)
                # reject rather than silently reorder overlaps
                for ev in other:
                    if any(not (ev.rec_out <= x.rec_in or ev.rec_in >= x.rec_out)
                           for x in base.events):
                        dialogs.show_error(
                            self.app.root, "Import aborted",
                            f"{os.path.basename(path)} overlaps the current "
                            f"list at event {ev.number}.")
                        return
                base.events.extend(other.events)
                merged += 1
            base.events.sort(key=lambda e: e.rec_in)
            base.renumber()
            self.app.refresh_view()
            self.app.status(f"Merged {merged} list(s), "
                            f"{len(base.events)} events")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Import failed", str(exc))

    def cmd_import_tab(self) -> None:
        """Import an EDL that was saved as tab-delimited columns."""
        if not self._need("edl", "Import Tab-Delimited EDL"):
            return
        path = self._open_path("Import EDL in Tab-Delimited Text")
        if not path:
            return
        try:
            from ..edl import parse_edl, write_edl

            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            # a tab-delimited list is rebuilt into EDL text, then parsed, so a
            # single validation path covers both sources
            text = self._tab_to_edl(raw)
            parsed = parse_edl(text, self.app.doc.rate, "CMX3600",
                               strict_roll_length=False)
            self.app.doc.edit_list.events.extend(parsed.events)
            self.app.doc.edit_list.renumber()
            self.app.refresh_view()
            self.app.status(f"Imported {len(parsed)} event(s)")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Import failed", str(exc))

    def _tab_to_edl(self, raw: str) -> str:
        """Convert tab-delimited rows into CMX3600 text."""
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        if not lines:
            raise ValueError("empty file")

        header = [h.strip().upper() for h in lines[0].split("\t")]
        index = {h: i for i, h in enumerate(header)}

        def cell(cells, name, default=""):
            i = index.get(name)
            return cells[i].strip() if i is not None and i < len(cells) else default

        out = ["TITLE:   Imported", "FCM: NON-DROP FRAME", ""]
        for n, line in enumerate(lines[1:], start=1):
            cells = line.split("\t")
            out.append(" ".join([
                f"{n:03d}",
                cell(cells, "ROLL", "AX"),
                cell(cells, "TRACK", "V"),
                cell(cells, "TRANSITION", "C"),
                cell(cells, "SRC_IN", "00:00:00:00"),
                cell(cells, "SRC_OUT", "00:00:00:00"),
                cell(cells, "REC_IN", "00:00:00:00"),
                cell(cells, "REC_OUT", "00:00:00:00"),
            ]))
            clip = cell(cells, "CLIP NAME") or cell(cells, "CLIP")
            if clip:
                out.append(f"* FROM CLIP NAME: {clip}")
        return "\n".join(out) + "\n"

    def cmd_import_excel(self) -> None:
        """Excel import: read CSV or a tab-delimited export."""
        if not self._need("edl", "Import EDL from Excel"):
            return
        path = filedialog.askopenfilename(
            parent=self.app.root, title="Import EDL from MS Excel",
            filetypes=[("CSV", "*.csv"), ("Text", "*.txt"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            if "\t" not in raw.splitlines()[0]:
                raw = "\n".join(
                    "\t".join(c.strip() for c in ln.split(","))
                    for ln in raw.splitlines())
            self._import_converted(raw)
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Import failed", str(exc))

    def _import_converted(self, raw: str) -> None:
        from ..edl import parse_edl

        text = self._tab_to_edl(raw)
        parsed = parse_edl(text, self.app.doc.rate, "CMX3600",
                           strict_roll_length=False)
        self.app.doc.edit_list.events.extend(parsed.events)
        self.app.doc.edit_list.renumber()
        self.app.refresh_view()
        self.app.status(f"Imported {len(parsed)} event(s)")

    def cmd_append_list(self) -> None:
        self.cmd_import_tab()

    def cmd_append_edl(self) -> None:
        self.cmd_import_edl()

    def cmd_append_aaf(self) -> None:
        self.cmd_import_aaf()

    def cmd_append_xml(self) -> None:
        self.cmd_import_xml()

    def cmd_wav_list(self) -> None:
        self.app.open_path(self._open_path("Open WAV File List"))

    def cmd_update_data(self) -> None:
        """Apply a versioned change list."""
        if not self._need("edl", "Update Data"):
            return
        path = filedialog.askopenfilename(
            parent=self.app.root, title="Load Change List",
            filetypes=[("Tab-delimited", "*.txt *.tsv"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            from ..update import UpdateSettings, apply_update, parse_change_list

            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            entries = parse_change_list(text, self.app.doc.rate)
            if not entries:
                dialogs.show_info(self.app.root, "Update Data",
                                  "No change entries found.")
                return

            result = apply_update(
                self.app.doc.edit_list, self.app.doc.rate, entries,
                UpdateSettings(update_timecodes_in=("destination", "source")))
            self.app.refresh_view()
            dialogs.show_info(self.app.root, "Update Data",
                              f"Applied {len(entries)} entr"
                              f"{'y' if len(entries) == 1 else 'ies'}.\n\n"
                              f"{result.summary()}")
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Update Data failed", str(exc))

    def cmd_pt_reconform(self) -> None:
        """Plan a re-conform against a new cut."""
        if not self._need("edl", "PT Re-conform"):
            return
        path = filedialog.askopenfilename(
            parent=self.app.root, title="Select the new cut",
            filetypes=[("EDL files", "*.edl"), ("All files", "*.*")])
        if not path:
            return
        try:
            from ..conform import ConformError, check_reconformable, plan_conform
            from ..edl import detect_dialect, parse_edl

            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            new_cut = parse_edl(text, self.app.doc.rate, detect_dialect(text),
                                strict_roll_length=False)

            problems = check_reconformable(self.app.doc.edit_list, new_cut)
            if problems:
                dialogs.show_error(
                    self.app.root, "Cannot re-conform",
                    "Blocking problems:\n\n  " + "\n  ".join(problems[:10]))
                return

            plan = plan_conform(self.app.doc.edit_list, new_cut)
            counts = plan.counts()
            lines = "\n".join(f"  {k:<10} {v}" for k, v in sorted(counts.items()))
            detail = "\n".join(
                f"  {op.kind.value:<8} {op.delta:+6d}  {op.detail}"
                for op in plan.ops if op.kind.value != "keep")[:2000]

            if dialogs.ask_yes_no(
                    self.app.root, "PT Re-conform",
                    f"Plan for {os.path.basename(path)}:\n\n{lines}\n\n"
                    f"{detail}\n\nApply this to the current list?"):
                new_events = [op.new for op in plan.ops if op.new is not None]
                self.app.doc.edit_list.events[:] = new_events
                self.app.doc.edit_list.events.sort(key=lambda e: e.rec_in)
                self.app.doc.edit_list.renumber()
                self.app.refresh_view()
                self.app.status(f"Re-conformed to {os.path.basename(path)}")
        except ConformError as exc:
            dialogs.show_error(self.app.root, "Cannot re-conform", str(exc))
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Re-conform failed", str(exc))

    def cmd_assemble_wavs(self) -> None:
        """Build a list from a folder of location WAV recordings."""
        folder = filedialog.askdirectory(
            parent=self.app.root, title="Select a folder of WAV files")
        if not folder:
            return
        try:
            from ..assembly import build_assembly, scan_wavs
            from ..timecode import rate_for

            paths = scan_wavs(folder)
            if not paths:
                dialogs.show_info(self.app.root, "Assemble WAVs",
                                  "No WAV files found in that folder.")
                return

            warnings: list[str] = []
            rate = rate_for(self.app.rate_var.get())
            result = build_assembly(paths, rate, warn=warnings)
            if not result.items:
                dialogs.show_info(
                    self.app.root, "Assemble WAVs",
                    "No WAV had a usable bext timestamp.\n\n"
                    + "\n".join(f"  {os.path.basename(p)}: {why}"
                                for p, why in result.skipped[:8]))
                return

            doc = guimodel.LoadedDocument(
                path=folder, kind=DocumentKind.EDL,
                edit_list=result.to_edit_list(), rate=result.rate)
            self.app.show_document(doc)

            msg = f"Assembled {len(result.items)} clip(s)."
            if result.skipped:
                msg += f"\n{len(result.skipped)} file(s) skipped."
            if warnings:
                msg += (f"\n\n{len(warnings)} warning(s):\n  "
                        + "\n  ".join(warnings[:5]))
            dialogs.show_info(self.app.root, "Assemble WAVs", msg)
        except Exception as exc:                    # noqa: BLE001
            dialogs.show_error(self.app.root, "Assembly failed", str(exc))

    def cmd_fit_columns(self) -> None:
        self.app.list.fit()

    def cmd_show_all_columns(self) -> None:
        for col in self.app.list.columns:
            self.app.list.set_visible(col.key, True)

    def cmd_default_columns(self) -> None:
        for col in self.app.list.columns:
            self.app.list.set_visible(col.key, col.visible)

    # ------------------------------------------------------------- misc

    def cmd_user_guide(self) -> None:
        self.app.status("User guide: see docs/SPEC.md in the repository")

    def cmd_about(self) -> None:
        dialogs.show_about(self.app.root)
