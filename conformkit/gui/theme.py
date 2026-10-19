"""Visual theme for the desktop UI.

Kept separate so the look can be changed, or a native-theme variant added,
without touching behaviour.
"""
from __future__ import annotations

from tkinter import ttk

__all__ = ["apply_theme", "FONT", "MONO"]

FONT = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 9, "bold")
FONT_TITLE = ("Segoe UI", 10, "bold")
MONO = ("Consolas", 9)

ROW_HEIGHT = 19
COLOR_ODD_ROW = "#fafafa"
COLOR_BAD = "#a40000"
COLOR_DIM = "#666666"
COLOR_TOOLTIP = "#ffffe1"


def apply_theme(root) -> ttk.Style:
    style = ttk.Style(root)
    for preferred in ("clam", "alt", "default"):
        try:
            style.theme_use(preferred)
            break
        except Exception:                           # noqa: BLE001
            continue

    style.configure(".", font=FONT)
    style.configure("Treeview", font=FONT, rowheight=ROW_HEIGHT)
    style.configure("Treeview.Heading", font=FONT_BOLD)
    style.configure("Toolbar.TButton", padding=(6, 3))
    style.configure("Title.TLabel", font=FONT_TITLE)
    style.configure("Dim.TLabel", foreground=COLOR_DIM)
    style.configure("Count.TLabel", font=FONT)
    style.configure("Mono.TLabel", font=MONO)
    return style
