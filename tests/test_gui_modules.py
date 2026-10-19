"""Tests for the modular GUI layer.

No display is required: panels, the command registry and the declarative
toolbar/menu specs are pure data. A single optional test constructs the window
when a display is available.
"""
import os

import pytest

from conformkit.gui import commands, panels
from conformkit.gui.commands import (
    COMMANDS, MENUS, SUBMENUS, TOOLBAR, Command, CommandRegistry,
    menu_items, toolbar_spec,
)
from conformkit.gui.panels import (
    PANELS, AafPanel, Column, EdlPanel, Filter, PtxPanel, WavPanel,
    panel_for, register_panel,
)

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures")
PROD = os.path.join(FIXTURE_DIR, "production")
EDL_SAMPLE = os.path.join(FIXTURE_DIR, "test_25.edl")
REAL_PTX = os.path.join(PROD, "GP04_DSS1.ptx")


# --------------------------------------------------------------- registry

def test_builtin_panels_registered():
    assert set(PANELS.kinds()) == {"edl", "ptx", "aaf", "wav"}


def test_panel_lookup():
    assert isinstance(panel_for("edl"), EdlPanel)
    assert isinstance(panel_for("ptx"), PtxPanel)
    assert panel_for("nope") is None


def test_registering_a_panel_is_additive():
    class CustomPanel:
        kind = "custom"

        def title(self, doc): return "x"
        def columns(self, doc): return []
        def rows(self, doc): return []
        def filters(self, doc): return []
        def counts(self, *a): return ""
        def match(self, *a): return True

    register_panel(CustomPanel())
    try:
        assert "custom" in PANELS
        assert panel_for("custom") is not None
    finally:
        PANELS._panels.pop("custom", None)


def test_command_registry_rejects_duplicates():
    reg = CommandRegistry()
    reg.register(Command("a", "A", lambda: None))
    with pytest.raises(ValueError):
        reg.register(Command("a", "A", lambda: None))


def test_command_gating():
    always = Command("x", "X", lambda: None)
    only_edl = Command("y", "Y", lambda: None, kinds=("edl",))
    assert always.enabled_for("ptx")
    assert only_edl.enabled_for("edl")
    assert not only_edl.enabled_for("ptx")


# --------------------------------------------------------------- columns

def test_edl_columns_have_fixed_anchors():
    cols = EdlPanel().columns(None)
    always = [c for c in cols if c.always]
    optional = [c for c in cols if not c.always]
    assert len(always) == 7
    assert [c.title for c in always][:3] == ["EvNo", "+", "RollName"]
    assert len(optional) == 11
    # the the reference tool column vocabulary
    titles = {c.title for c in cols}
    for expected in ("Source In", "Source Out", "Destination In",
                     "Destination Out", "Clip Name", "Bad Speed"):
        assert expected in titles


def test_edl_optional_column_defaults():
    cols = {c.key: c for c in EdlPanel().columns(None)}
    assert cols["clip"].visible is True
    assert cols["comments"].visible is False
    assert cols["vfx"].visible is False


def test_ptx_and_aaf_columns():
    assert PtxPanel().columns(None)[1].title == "Track"
    assert AafPanel().columns(None)[1].title == "Name"
    assert WavPanel().columns(None)[1].title == "Field"


# --------------------------------------------------------------- rows

@pytest.mark.skipif(not os.path.exists(EDL_SAMPLE), reason="no EDL fixture")
def test_edl_rows_match_columns():
    from conformkit.guimodel import load_document

    doc = load_document(EDL_SAMPLE, "25")
    panel = EdlPanel()
    rows = panel.rows(doc)
    columns = panel.columns(doc)
    assert len(rows) == len(doc.edit_list)
    assert len(rows[0]) == len(columns)


@pytest.mark.skipif(not os.path.exists(REAL_PTX), reason="no production PTX")
def test_ptx_rows():
    from conformkit.guimodel import load_document

    doc = load_document(REAL_PTX, "25")
    panel = PtxPanel()
    rows = panel.rows(doc)
    assert len(rows) == len(doc.session.track_names)
    assert len(rows[0]) == len(panel.columns(doc))


# --------------------------------------------------------------- filters

@pytest.mark.skipif(not os.path.exists(EDL_SAMPLE), reason="no EDL fixture")
def test_edl_track_filter():
    from conformkit.guimodel import load_document

    doc = load_document(EDL_SAMPLE, "25")
    panel = EdlPanel()
    rows = panel.rows(doc)
    specs = {f.key: f for f in panel.filters(doc)}
    assert "track" in specs and "roll" in specs and "search" in specs
    assert specs["track"].values[0] == "All"

    kept = [r for r in rows if panel.match(doc, r, {"track": "V"})]
    assert len(kept) < len(rows)
    assert all("V" in r[7].split("/") for r in kept)


@pytest.mark.skipif(not os.path.exists(EDL_SAMPLE), reason="no EDL fixture")
def test_edl_search_matches_any_column():
    from conformkit.guimodel import load_document

    doc = load_document(EDL_SAMPLE, "25")
    panel = EdlPanel()
    rows = panel.rows(doc)
    kept = [r for r in rows if panel.match(doc, r, {"search": "BL"})]
    assert kept


@pytest.mark.skipif(not os.path.exists(EDL_SAMPLE), reason="no EDL fixture")
def test_counts_text_is_non_empty():
    from conformkit.guimodel import load_document

    doc = load_document(EDL_SAMPLE, "25")
    text = EdlPanel().counts(doc, 13, 13, 0)
    assert "Events 13" in text
    assert "Frame Count" in text


# --------------------------------------------------------------- specs

def test_toolbar_spec_references_real_commands():
    """Every toolbar entry must resolve after the operations register."""
    from conformkit.gui.operations import Operations

    class FakeApp:
        def status(self, _t): pass

    Operations(FakeApp()).register()
    for entry in toolbar_spec():
        if entry[0] == "cmd":
            assert COMMANDS.get(entry[1]) is not None, entry
        elif entry[0] == "menu":
            assert entry[1] in SUBMENUS


def test_menu_specs_resolve():
    from conformkit.gui.operations import Operations

    class FakeApp:
        def status(self, _t): pass

    Operations(FakeApp()).register()
    for name in list(MENUS) + list(SUBMENUS):
        for item in menu_items(name):
            if item is None:
                continue
            label, cmd_id = item
            assert label
            assert COMMANDS.get(cmd_id) is not None


def test_toolbar_has_the_reference_commands():
    """The toolbar mirrors the reference product's command order."""
    ids = [e[1] for e in TOOLBAR if e[0] == "cmd"]
    for expected in ("open", "save", "clear_events", "display_options",
                     "clean_events", "compare_lists", "wav_list",
                     "assemble_wavs", "update_data", "pt_reconform",
                     "user_guide"):
        assert expected in ids


def test_export_menu_targets():
    names = [i[1] for i in SUBMENUS["export"] if i and i[0] == "cmd"]
    for expected in ("export_edl", "export_ptx", "export_piccut",
                     "export_midi", "export_tab", "export_excel",
                     "export_offset"):
        assert expected in names


# --------------------------------------------------------------- window

def test_window_builds_when_display_available():
    """Optional smoke test: construct the shell if Tk can open a window."""
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except Exception as exc:                        # noqa: BLE001
        pytest.skip(f"no display: {exc}")

    try:
        root.withdraw()
        from conformkit.gui.app import ConformKitApp

        app = ConformKitApp(root)
        assert app.document_kind() == ""
        assert len(COMMANDS.all()) >= 30
        assert app.toolbar is not None
        assert app.list is not None
        assert app.status_bar is not None
    finally:
        root.destroy()


def test_gui_package_does_not_import_tk_on_import():
    import subprocess
    import sys

    code = ("import sys, conformkit.gui; "
            "print('tkinter' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True,
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"
