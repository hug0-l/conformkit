"""Dialog windows.

Each dialog is a function `show_<name>(parent, **context) -> result | None`.
They are independent of the shell, so a dialog can be added or reworked
without touching it.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import theme

__all__ = ["show_display_options", "show_about", "show_error", "show_info",
           "ask_yes_no", "show_compare_summary"]


def show_display_options(parent, columns) -> dict[str, bool] | None:
    """Checkbox list of hideable columns. Returns the new visibility or None."""
    win = tk.Toplevel(parent)
    win.title("Display Options")
    win.transient(parent)
    win.resizable(False, False)

    ttk.Label(win, text="Columns shown in the list",
              font=theme.FONT_BOLD).grid(
        row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 6))

    vars_: dict[str, tk.BooleanVar] = {}
    row = 1
    for col in columns:
        if col.always:
            continue
        var = tk.BooleanVar(value=col.visible)
        vars_[col.key] = var
        ttk.Checkbutton(win, text=col.title, variable=var).grid(
            row=row, column=0, sticky="w", padx=12, pady=1)
        row += 1

    always = [c.title for c in columns if c.always]
    ttk.Label(win, text=f"{', '.join(always)} are always shown.",
              style="Dim.TLabel").grid(
        row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(8, 0))
    row += 1

    result: dict[str, bool] = {}

    def apply() -> None:
        for key, var in vars_.items():
            result[key] = var.get()
        win.destroy()

    btns = ttk.Frame(win)
    btns.grid(row=row, column=0, columnspan=2, pady=10)
    ttk.Button(btns, text="OK", command=apply).pack(side="left", padx=4)
    ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left")

    win.wait_window()
    return result or None


def show_compare_summary(parent, other_name: str, changes) -> None:
    counts = changes.counts() if hasattr(changes, "counts") else {}
    lines = "\n".join(f"  {k:<10} {v}" for k, v in sorted(counts.items()))
    total = len(changes) if hasattr(changes, "__len__") else 0

    show_info(parent, "Compare Lists",
              f"{other_name}\n\n{total} change(s)\n\n"
              f"{lines or '  no differences'}")


def show_info(parent, title: str, message: str) -> None:
    messagebox.showinfo(title, message, parent=parent)


def show_error(parent, title: str, message: str) -> None:
    messagebox.showerror(title, message, parent=parent)


def ask_yes_no(parent, title: str, message: str) -> bool:
    return bool(messagebox.askyesno(title, message, parent=parent))


def show_about(parent) -> None:
    show_info(
        parent, "About conformkit",
        "conformkit \u2014 edit list and conform toolkit\n\n"
        "Reads EDL, Pro Tools sessions and AAF.\n"
        "Layout and terminology follow the the reference tool workflow.\n\n"
        "Not affiliated with the vendor or Avid.")
