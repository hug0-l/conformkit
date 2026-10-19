"""Application shell.

Wires the widgets, the panel registry, the command registry and the background
loader together. It contains no format knowledge and no command bodies, so a
new format is a panel and a new action is a command — never a change here.
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import ttk

from .. import guimodel
from ..guimodel import load_document
from . import dialogs, theme
from .commands import COMMANDS, MENUS, menu_items
from .operations import Operations
from .panels import panel_for
from .widgets import DocumentList, FilterRow, StatusBar, Toolbar

__all__ = ["ConformKitApp", "main"]

RATE_CHOICES = ["23.976", "24", "25", "29.97", "29.97df", "30",
                "47.952", "48", "59.94", "59.94df", "60"]


class ConformKitApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("conformkit \u2014 Edit List")
        self._size_to_screen()
        self.root.minsize(980, 560)

        theme.apply_theme(root)

        self.doc: guimodel.LoadedDocument | None = None
        self.rows: list[tuple] = []
        self._columns: list = []
        self._bad_index: int | None = None

        self.rate_var = tk.StringVar(
            value=os.environ.get("CONFORMKIT_RATE", "25"))
        self.list_title = tk.StringVar(value="Untitled")

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None

        self.ops = Operations(self)
        self.ops.register()

        self._build_menu()
        self._build_toolbar()
        self._build_header()
        self._build_list()
        self._build_status()
        self._poll_queue()
        self._set_document(None)

    # ------------------------------------------------------------ geometry

    def _size_to_screen(self) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w = min(1360, sw - 80)
        h = min(800, sh - 120)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------------------------------------------------------------- menu

    def _build_menu(self) -> None:
        bar = tk.Menu(self.root)
        self._menu_widgets: dict[str, tk.Menu] = {}

        for title in MENUS:
            menu = tk.Menu(bar, tearoff=0)
            self._menu_widgets[title] = menu
            bar.add_cascade(label=title, menu=menu)

        self.root.configure(menu=bar)
        self._refresh_menus()

    def _refresh_menus(self) -> None:
        kind = self.document_kind()
        for title, menu in self._menu_widgets.items():
            menu.delete(0, "end")
            for item in menu_items(title):
                if item is None:
                    menu.add_separator()
                    continue
                label, cmd_id = item
                cmd = COMMANDS.get(cmd_id)
                state = "normal" if cmd and cmd.enabled_for(kind) else "disabled"
                menu.add_command(
                    label=label, state=state,
                    command=lambda c=cmd_id: self.run(c))

    # ------------------------------------------------------------- widgets

    def _build_toolbar(self) -> None:
        self.toolbar = Toolbar(self.root, self)
        self.toolbar.pack(side="top", fill="x")

        rate_box = ttk.Frame(self.toolbar)
        rate_box.pack(side="right")
        ttk.Label(rate_box, text="Frame Count",
                  style="Dim.TLabel").pack(side="left")
        ttk.Combobox(rate_box, textvariable=self.rate_var, width=8,
                     values=RATE_CHOICES, state="readonly").pack(
            side="left", padx=4)

    def _build_header(self) -> None:
        outer = ttk.Frame(self.root, padding=(6, 2, 6, 2))
        outer.pack(side="top", fill="x")

        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, textvariable=self.list_title,
                  style="Title.TLabel").pack(side="left")
        self.file_label = ttk.Label(row, text="", style="Dim.TLabel")
        self.file_label.pack(side="left", padx=10)

        self.filters = FilterRow(outer, on_change=self._refilter)
        self.filters.pack(fill="x", pady=(3, 0))

    def _build_list(self) -> None:
        self.list = DocumentList(self.root, on_select=self._update_counts)
        self.list.pack(side="top", fill="both", expand=True)

    def _build_status(self) -> None:
        self.status_bar = StatusBar(self.root)
        self.status_bar.pack(side="bottom", fill="x")

    # -------------------------------------------------------------- state

    def document_kind(self) -> str:
        return self.doc.kind if self.doc else ""

    def status(self, text: str) -> None:
        self.status_bar.set_status(text)

    def column_index(self, key: str) -> int:
        for i, col in enumerate(self._columns):
            if col.key == key:
                return i
        return 0

    def run(self, command_id: str) -> None:
        cmd = COMMANDS.get(command_id)
        if cmd is None:
            self.status(f"unknown command: {command_id}")
            return
        if not cmd.enabled_for(self.document_kind()):
            self.status(f"{cmd.label} needs a different document")
            return
        cmd.handler()

    # -------------------------------------------------------------- load

    def open_path(self, path: str) -> None:
        if self._worker and self._worker.is_alive():
            return

        self.status_bar.set_status(f"Loading {os.path.basename(path)}…")
        self.status_bar.set_progress(0.02)
        rate = self.rate_var.get()

        def work() -> None:
            def progress(frac: float, msg: str) -> None:
                self._queue.put(("progress", frac, msg))

            doc = load_document(path, rate, progress=progress)
            self._queue.put(("done", doc))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _poll_queue(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "progress":
                    self.status_bar.set_progress(item[1])
                    self.status_bar.set_status(item[2])
                elif item[0] == "done":
                    self.show_document(item[1])
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    # ------------------------------------------------------------- render

    def show_document(self, doc: guimodel.LoadedDocument) -> None:
        self.doc = doc
        self.status_bar.set_progress(1.0)
        self.file_label.configure(text=doc.name or "")

        if doc.error:
            self.status_bar.set_status(f"Failed: {doc.error}")
            self.rows = []
            self._columns = []
            self.list.configure_columns([])
            self.list.fill([])
            self.status_bar.set_counts("")
            self._refresh_menus()
            self.toolbar.refresh()
            dialogs.show_error(self.root, "Cannot open file", doc.error)
            return

        panel = panel_for(doc.kind)
        if panel is None:
            self.status_bar.set_status(f"no view for {doc.kind}")
            return

        self._set_document(panel)
        self.list_title.set(panel.title(doc))
        self.rows = panel.rows(doc)
        self.filters.build(panel.filters(doc))
        self._refilter()
        self.status_bar.set_status("Ready")

    def _set_document(self, panel) -> None:
        """Install a panel's columns. `panel=None` clears the view."""
        if panel is None:
            self._columns = []
            self.list.configure_columns([])
            self.filters.build([])
            self._refresh_menus()
            return

        doc = self.doc
        self._columns = panel.columns(doc) if doc else []
        self.list.configure_columns(self._columns)
        self._bad_index = self.column_index("bad_speed") \
            if any(c.key == "bad_speed" for c in self._columns) else None
        self._refresh_menus()
        self.toolbar.refresh()

    def refresh_view(self) -> None:
        """Re-project the current document, keeping the panel."""
        if not self.doc:
            return
        panel = panel_for(self.doc.kind)
        if panel is None:
            return
        self.rows = panel.rows(self.doc)
        self.list_title.set(panel.title(self.doc))
        self._refilter()

    def _refilter(self) -> None:
        panel = panel_for(self.document_kind()) if self.doc else None
        if panel is None:
            self.list.fill([])
            self.status_bar.set_counts("")
            return

        filters = self.filters.values()
        rows = [r for r in self.rows if panel.match(self.doc, r, filters)]
        self.list.fill(rows, bad_index=self._bad_index)
        self._update_counts()

    def _update_counts(self) -> None:
        panel = panel_for(self.document_kind()) if self.doc else None
        if panel is None:
            return
        self.status_bar.set_counts(panel.counts(
            self.doc, len(self.rows),
            self.list.shown_count(), self.list.selection_count()))


def main() -> int:
    root = tk.Tk()
    try:
        root.call("tk", "scaling", 1.2)
    except tk.TclError:
        pass

    app = ConformKitApp(root)

    args = [a for a in os.sys.argv[1:] if os.path.exists(a)]
    if args:
        root.after(120, lambda: app.open_path(args[0]))

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
