# Desktop UI

An conformkit window, rebuilt from the real product's toolbar order, menu
structure, column set and terminology so a user who knows the reference tool does not have
to relearn navigation.

```
+-------------------------------------------------------------------+
| File  Edit  List  View  Help                                      |
+-------------------------------------------------------------------+
| Open List | Save List | Import▾ | Append▾ | Clear Events |        |
| Display Options | Clean Events | Compare Lists |                  |
| WAV File List | Assemble WAVs | Update Data | PT Re-conform |     |
| Export▾ | User Guide            Frame Count [25 v]                 |
+-------------------------------------------------------------------+
| Sequence 01   (3 warning(s))                       test_25.edl     |
| Track [All v]  Roll [    ]  Search [        ]  Clear Filters      |
+-------------------------------------------------------------------+
| EvNo | + | RollName | Source In | Source Out | Destination In | ...|
+-------------------------------------------------------------------+
| Events 13   Rolls 2   Frame Count 25                              |
+-------------------------------------------------------------------+
```

## Why it is modular

Four extension points, none of which require editing the shell:

| You want to add | Do this | File |
|---|---|---|
| A new document format | write a panel, `register_panel(...)` | `panels.py` |
| A new user action | write a method, add a `_COMMANDS` row | `operations.py` |
| A new toolbar button or menu item | add an entry to `TOOLBAR` / `MENUS` | `commands.py` |
| A new dialog | write `show_<name>()` | `dialogs.py` |

## Layers

```
app.py         shell: wires widgets, owns state, nothing else
  ├── commands.py    command registry + declarative TOOLBAR / MENUS
  ├── operations.py  one method per command (the only place with behaviour)
  ├── panels.py      one panel per document kind (columns, rows, filters)
  ├── widgets.py     Toolbar, FilterRow, DocumentList, StatusBar
  ├── dialogs.py     independent dialogs
  └── theme.py       fonts, colours, ttk styles
```

`guimodel.py` (one level up) does the file loading and stays Tk-free, so it is
tested headless.

## Adding a format

```python
# conformkit/gui/panels.py

class OtioPanel:
    kind = "otio"

    def title(self, doc):            return doc.name
    def columns(self, doc):          return [Column("name", "Name", 300, always=True)]
    def rows(self, doc):             return [(t.name,) for t in doc.timeline.tracks]
    def filters(self, doc):          return [Filter("search", "Search", [], 22)]
    def counts(self, doc, total, shown, selected):
        return f"Tracks {total}"
    def match(self, doc, row, filters):
        q = filters.get("search", "").casefold()
        return not q or q in row[0].casefold()

register_panel(OtioPanel())
```

Then teach `guimodel._sniff` and `guimodel.load_document` about the new kind.
No change to `app.py`.

## Adding a command

```python
# conformkit/gui/operations.py

# 1. in _COMMANDS
("export_otio", "OTIO…", "Export as OpenTimelineIO", ("edl",)),

# 2. a method
def cmd_export_otio(self) -> None:
    if not self._need("edl", "Export OTIO"):
        return
    ...
```

Then reference `("cmd", "export_otio")` from `commands.MENUS["Export"]` or
`commands.TOOLBAR`.

## Command gating

Each command declares which document kinds it applies to:

```python
Command("export_edl", "EDL…", handler, kinds=("edl",))
```

Buttons and menu items disable themselves automatically when the open document
is a different kind. An empty `kinds` tuple means always available.

## Threading

`load_document` runs on a worker thread; the shell drains a queue on the Tk
main loop. Progress arrives as `(fraction, message)` callbacks, so parsing code
never touches a widget.

Parsing a 34 MB PTX takes ~7 s; the window stays responsive and the progress
bar tracks the phase.

## Testing

`tests/test_gui_modules.py` covers panels, the command registry, the
declarative specs and (when a display exists) window construction.
`tests/test_gui_model.py` covers loading and row projection. Both run headless
except one optional smoke test.

## Appearance

Plain Tk widgets and the `clam` ttk theme. No vendor icons or artwork are used.
The familiarity comes from layout, column names and terminology, which is the
part that reduces learning cost.
