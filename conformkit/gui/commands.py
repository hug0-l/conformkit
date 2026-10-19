"""Command registry.

Every user action is a `Command`. Adding a feature means registering one here
and referencing its id from a toolbar entry or a menu — no edits to the shell.

`kinds` gates availability: an empty tuple means always enabled, otherwise the
command is enabled only when the open document is one of those kinds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

__all__ = ["Command", "CommandRegistry", "COMMANDS", "register",
           "register_command", "menu_items", "toolbar_spec"]


@dataclass
class Command:
    id: str
    label: str
    handler: Callable
    tooltip: str = ""
    kinds: tuple[str, ...] = ()     # empty = always available
    shortcut: str = ""

    def enabled_for(self, kind: str) -> bool:
        return not self.kinds or kind in self.kinds


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(self, command: Command) -> Command:
        if command.id in self._commands:
            raise ValueError(f"duplicate command id: {command.id}")
        self._commands[command.id] = command
        return command

    def get(self, command_id: str) -> Command | None:
        return self._commands.get(command_id)

    def all(self) -> list[Command]:
        return list(self._commands.values())

    def __contains__(self, command_id: str) -> bool:
        return command_id in self._commands


COMMANDS = CommandRegistry()


def register_command(command: Command) -> Command:
    return COMMANDS.register(command)


def register(command_id: str, label: str, **kwargs) -> Command:
    """Convenience form: `register("open", "Open List", handler=fn)`."""
    return COMMANDS.register(Command(id=command_id, label=label, **kwargs))


# ---------------------------------------------------------------- specs
#
# Declarative descriptions of the toolbar and menus. They reference command
# ids only, so the shell never needs to know what a command does.

TOOLBAR: list[tuple] = [
    # ("cmd", id) | ("menu", id, label) | ("sep",)
    ("cmd", "open"),
    ("cmd", "save"),
    ("menu", "import", "Import"),
    ("menu", "append", "Append"),
    ("sep",),
    ("cmd", "clear_events"),
    ("cmd", "display_options"),
    ("cmd", "clean_events"),
    ("cmd", "compare_lists"),
    ("sep",),
    ("cmd", "wav_list"),
    ("cmd", "assemble_wavs"),
    ("sep",),
    ("cmd", "update_data"),
    ("sep",),
    ("cmd", "pt_reconform"),
    ("sep",),
    ("menu", "export", "Export"),
    ("sep",),
    ("cmd", "user_guide"),
]

MENUS: dict[str, list] = {
    "File": [
        ("cmd", "new_edl"),
        ("cmd", "open"),
        ("cmd", "save"),
        ("sep",),
        ("cmd", "quit"),
    ],
    "Edit": [
        ("cmd", "clear_events"),
        ("cmd", "clean_events"),
        ("sep",),
        ("cmd", "display_options"),
    ],
    "List": [
        ("cmd", "compare_lists"),
        ("cmd", "copy_list_as"),
        ("cmd", "export_edl"),
    ],
    "View": [
        ("cmd", "fit_columns"),
        ("cmd", "show_all_columns"),
        ("cmd", "default_columns"),
    ],
    "Help": [
        ("cmd", "about"),
    ],
}

SUBMENUS: dict[str, list] = {
    "import": [
        ("cmd", "import_edl"),
        ("cmd", "import_tab"),
        ("cmd", "import_excel"),
        ("cmd", "import_multi"),
        ("sep",),
        ("cmd", "import_aaf"),
        ("cmd", "import_xml"),
        ("sep",),
        ("cmd", "import_ptx"),
    ],
    "append": [
        ("cmd", "append_list"),
        ("cmd", "append_edl"),
        ("cmd", "append_aaf"),
        ("cmd", "append_xml"),
    ],
    "export": [
        ("cmd", "export_edl"),
        ("cmd", "export_aaf"),
        ("cmd", "export_ptx"),
        ("cmd", "export_piccut"),
        ("sep",),
        ("cmd", "export_midi"),
        ("sep",),
        ("cmd", "export_tab"),
        ("cmd", "export_excel"),
        ("cmd", "export_offset"),
    ],
}


def menu_items(name: str) -> list:
    """Resolve a menu name to (label, command_id) pairs, with separators."""
    spec = MENUS.get(name) or SUBMENUS.get(name) or []
    out = []
    for entry in spec:
        if entry[0] == "sep":
            out.append(None)
            continue
        cmd = COMMANDS.get(entry[1])
        if cmd is None:
            continue
        out.append((cmd.label, cmd.id))
    return out


def toolbar_spec() -> list:
    """Resolve the toolbar spec into concrete entries."""
    out = []
    for entry in TOOLBAR:
        if entry[0] == "sep":
            out.append(("sep",))
        elif entry[0] == "cmd":
            cmd = COMMANDS.get(entry[1])
            if cmd:
                out.append(("cmd", cmd.id, cmd.label, cmd.tooltip))
        elif entry[0] == "menu":
            out.append(("menu", entry[1], entry[2]))
    return out
