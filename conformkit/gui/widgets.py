"""Reusable widgets.

Each widget takes a `controller` and calls only the small surface it needs, so
widgets can be swapped or reused in another window.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme
from .commands import toolbar_spec

__all__ = ["Toolbar", "FilterRow", "DocumentList", "StatusBar", "Tooltip"]


class Tooltip:
    """Minimal tooltip; avoids a third-party dependency for one feature."""

    def __init__(self, widget: tk.Widget, text: str, delay: int = 350):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after: str | None = None
        self._win: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        if self._after is None:
            self._after = self.widget.after(self.delay, self._show)

    def _show(self) -> None:
        self._after = None
        if self._win or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        win = tk.Toplevel(self.widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{x}+{y}")
        tk.Label(win, text=self.text, background=theme.COLOR_TOOLTIP,
                 relief="solid", borderwidth=1,
                 font=("Segoe UI", 8)).pack()
        self._win = win

    def _hide(self, _event=None) -> None:
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._win is not None:
            self._win.destroy()
            self._win = None


class Toolbar(ttk.Frame):
    """The command bar. Purely driven by `commands.toolbar_spec()`."""

    def __init__(self, master, controller, **kw):
        super().__init__(master, padding=(6, 5, 6, 2), **kw)
        self.controller = controller
        self.menu_buttons: dict[str, ttk.Button] = {}
        self.menus: dict[str, tk.Menu] = {}
        self._buttons: list[ttk.Button] = []
        self.build()

    def build(self) -> None:
        for widget in self.winfo_children():
            widget.destroy()
        self.menu_buttons.clear()
        self.menus.clear()
        self._buttons.clear()

        for entry in toolbar_spec():
            if entry[0] == "sep":
                ttk.Separator(self, orient="vertical").pack(
                    side="left", fill="y", padx=4)
                continue

            if entry[0] == "cmd":
                _, cmd_id, label, tip = entry
                btn = ttk.Button(self, text=label, style="Toolbar.TButton",
                                 command=lambda c=cmd_id: self.controller.run(c))
                btn.pack(side="left", padx=1)
                self._buttons.append(btn)
                if tip:
                    Tooltip(btn, tip)
            else:
                _, menu_id, label = entry
                menu = tk.Menu(self, tearoff=0)
                self.menus[menu_id] = menu
                btn = ttk.Button(self, text=f"{label} \u25be",
                                 style="Toolbar.TButton",
                                 command=lambda m=menu_id: self.popup(m))
                btn.pack(side="left", padx=1)
                self.menu_buttons[menu_id] = btn
                self._buttons.append(btn)

        self.refresh()

    def refresh(self) -> None:
        """Rebuild menu contents and enable/disable buttons."""
        from .commands import COMMANDS, menu_items

        kind = self.controller.document_kind()
        for menu_id, menu in self.menus.items():
            menu.delete(0, "end")
            for item in menu_items(menu_id):
                if item is None:
                    menu.add_separator()
                    continue
                label, cmd_id = item
                cmd = COMMANDS.get(cmd_id)
                state = "normal" if cmd and cmd.enabled_for(kind) else "disabled"
                menu.add_command(
                    label=label, state=state,
                    command=lambda c=cmd_id: self.controller.run(c))

        # enable/disable plain command buttons
        for entry, btn in zip(
                [e for e in toolbar_spec() if e[0] != "sep"], self._buttons):
            if entry[0] != "cmd":
                continue
            cmd = COMMANDS.get(entry[1])
            state = "normal" if cmd and cmd.enabled_for(kind) else "disabled"
            btn.configure(state=state)

    def popup(self, menu_id: str) -> None:
        btn = self.menu_buttons[menu_id]
        menu = self.menus[menu_id]
        try:
            menu.tk_popup(btn.winfo_rootx(),
                          btn.winfo_rooty() + btn.winfo_height())
        finally:
            menu.grab_release()


class FilterRow(ttk.Frame):
    """Track / Roll / Search filters, built from the active panel's spec.

    Emits `on_change` when any filter changes.
    """

    def __init__(self, master, on_change, **kw):
        super().__init__(master, padding=(6, 2, 6, 2), **kw)
        self.on_change = on_change
        self.vars: dict[str, tk.StringVar] = {}
        self._widgets: dict[str, tk.Widget] = {}
        self._specs: dict[str, object] = {}

    def build(self, filters) -> None:
        for widget in self.winfo_children():
            widget.destroy()
        self.vars.clear()
        self._widgets.clear()
        self._specs.clear()

        for spec in filters:
            var = tk.StringVar(value=spec.values[0] if spec.values else "")
            self.vars[spec.key] = var
            self._specs[spec.key] = spec

            ttk.Label(self, text=spec.label, style="Dim.TLabel").pack(
                side="left")
            if spec.values:
                widget = ttk.Combobox(self, textvariable=var,
                                      values=spec.values, width=spec.width,
                                      state="readonly")
            else:
                widget = ttk.Entry(self, textvariable=var, width=spec.width)
            widget.pack(side="left", padx=(4, 10))
            self._widgets[spec.key] = widget
            var.trace_add("write", lambda *_: self.on_change())

        if filters:
            ttk.Button(self, text="Clear Filters",
                       command=self.clear).pack(side="left")

    def clear(self) -> None:
        for key, var in self.vars.items():
            spec = self._specs.get(key)
            var.set(spec.values[0] if spec and spec.values else "")

    def values(self) -> dict[str, str]:
        return {k: v.get() for k, v in self.vars.items()}

    def set_track_values(self, values: list[str]) -> None:
        widget = self._widgets.get("track")
        if isinstance(widget, ttk.Combobox):
            widget.configure(values=values)
            var = self.vars.get("track")
            if var is not None:
                var.set("All")


class DocumentList(ttk.Frame):
    """The main table. Columns come from the active panel."""

    def __init__(self, master, on_select=None, **kw):
        super().__init__(master, padding=(6, 0, 6, 0), **kw)
        self.on_select = on_select
        self.tree = ttk.Treeview(self, show="headings", selectmode="extended")
        self._yscroll = ttk.Scrollbar(self, orient="vertical",
                                      command=self.tree.yview)
        self._xscroll = ttk.Scrollbar(self, orient="horizontal",
                                      command=self.tree.xview)
        self.tree.configure(yscrollcommand=self._yscroll.set,
                            xscrollcommand=self._xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self._yscroll.grid(row=0, column=1, sticky="ns")
        self._xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.tree.tag_configure("odd", background=theme.COLOR_ODD_ROW)
        self.tree.tag_configure("bad", foreground=theme.COLOR_BAD)
        if on_select:
            self.tree.bind("<<TreeviewSelect>>",
                           lambda _e: on_select())

        self._columns: list = []
        self._visible: dict[str, bool] = {}

    def configure_columns(self, columns) -> None:
        self._columns = list(columns)
        keys = [c.key for c in self._columns]

        self.tree.configure(columns=keys)
        for col in self._columns:
            self.tree.heading(col.key, text=col.title, anchor=col.anchor)
            self.tree.column(col.key, width=col.width, anchor=col.anchor,
                             stretch=col.stretch)

        self._visible = {c.key: c.visible for c in self._columns}
        self.apply_visibility()

    @property
    def columns(self) -> list:
        return list(self._columns)

    def apply_visibility(self) -> None:
        shown = [c.key for c in self._columns if self._visible.get(c.key, True)]
        self.tree.configure(displaycolumns=shown or ["#all"])

    def set_visible(self, key: str, visible: bool) -> None:
        self._visible[key] = visible
        self.apply_visibility()

    def visibility(self) -> dict[str, bool]:
        return dict(self._visible)

    def fill(self, rows: list[tuple], bad_index: int | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(rows):
            tags = []
            if i % 2:
                tags.append("odd")
            if bad_index is not None and row[bad_index]:
                tags.append("bad")
            self.tree.insert("", "end", values=row, tags=tags)

    def selection_count(self) -> int:
        return len(self.tree.selection())

    def shown_count(self) -> int:
        return len(self.tree.get_children())

    def fit(self) -> None:
        for col in self._columns:
            self.tree.column(col.key, width=col.width)


class StatusBar(ttk.Frame):
    """Counts on the left, activity and progress on the right."""

    def __init__(self, master, **kw):
        super().__init__(master, padding=(6, 4), **kw)
        self.counts_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.progress_var = tk.DoubleVar()

        ttk.Label(self, textvariable=self.counts_var,
                  style="Count.TLabel").pack(side="left")
        self.progress = ttk.Progressbar(self, variable=self.progress_var,
                                        maximum=100.0, length=120)
        self.progress.pack(side="right", padx=8)
        ttk.Label(self, textvariable=self.status_var,
                  style="Dim.TLabel").pack(side="right")

    def set_counts(self, text: str) -> None:
        self.counts_var.set(text)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def set_progress(self, fraction: float) -> None:
        self.progress_var.set(fraction * 100)
