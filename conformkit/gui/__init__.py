"""Desktop UI package.

Public surface:

    from conformkit.gui import main, ConformKitApp

Extension points:

    conformkit.gui.panels.register_panel   add a document format view
    conformkit.gui.commands.register_command   add a user action
    conformkit.gui.commands.TOOLBAR / MENUS    place it in the UI

Importing this package does not import `tkinter`; the shell is imported lazily
so the library stays usable on machines without a display.
"""
from __future__ import annotations

__all__ = ["main", "ConformKitApp", "panels", "commands", "widgets",
           "dialogs", "theme", "operations"]


def __getattr__(name):
    """Import the Tk-dependent modules only when they are asked for."""
    if name == "main":
        from .app import main as _main
        return _main
    if name == "ConformKitApp":
        from .app import ConformKitApp as _app
        return _app
    if name in ("panels", "commands", "widgets", "dialogs", "theme",
                "operations", "app"):
        import importlib
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(name)
