"""Tests for the GUI's command building.

The GUI is a thin launcher: it shells out to the same scripts you would run by
hand. Building those commands is the only real logic, and it needs no display,
so it is what these tests cover.
"""

import json

import pytest

pytest.importorskip("tkinter", reason="tkinter is not installed in this environment")

import tkinter as tk  # noqa: E402

import botc_gui  # noqa: E402

from botc_gui import (  # noqa: E402
    ALL_SCRIPTS,
    available_scripts,
    build_checklist_command,
    build_generate_command,
    build_import_command,
)


def _tail(command):
    """Arguments after the interpreter and script path."""
    return command[3:]


def test_generate_defaults_to_every_role():
    assert _tail(build_generate_command(script=ALL_SCRIPTS)) == []


def test_generate_passes_the_chosen_script():
    assert _tail(build_generate_command(script="Catfishing")) == [
        "--script",
        "Catfishing",
    ]


def test_a_script_file_takes_priority_over_the_dropdown():
    command = _tail(build_generate_command(script="Catfishing", script_file="s.json"))

    assert command == ["--script-file", "s.json"]  # not both


def test_generate_options_map_to_flags():
    command = _tail(
        build_generate_command(
            only="Imp", skip_stl=True, no_reminders=True, no_3mf=True, art_up=True
        )
    )

    assert command == [
        "--only",
        "Imp",
        "--skip-stl",
        "--no-reminders",
        "--no-3mf",
        "--art-up",
    ]


def test_checklist_merges_by_default():
    """The GUI must never silently discard ticks."""
    assert "--merge" in build_checklist_command()


def test_checklist_can_target_another_file_without_merging_into_it():
    command = _tail(
        build_checklist_command(script="Catfishing", output="scoped.xlsx", merge=False)
    )

    assert command == ["--script", "Catfishing", "--output", "scoped.xlsx"]
    assert "--merge" not in command


def test_import_command_takes_several_files():
    assert _tail(build_import_command(["a.json", "b.json"])) == ["a.json", "b.json"]


def test_available_scripts_reads_names_and_skips_comments(tmp_path):
    path = tmp_path / "scripts.json"
    path.write_text(json.dumps({"_comment": "docs", "B": [], "A": []}))

    assert available_scripts(path) == ["A", "B"]  # sorted, no _comment


def test_available_scripts_survives_a_missing_or_broken_file(tmp_path):
    missing = tmp_path / "nope.json"
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")

    # The GUI must still open when scripts.json is absent or being edited.
    assert available_scripts(missing) == []
    assert available_scripts(broken) == []


def test_available_scripts_tolerates_a_bom(tmp_path):
    path = tmp_path / "scripts.json"
    path.write_text(json.dumps({"A": []}), encoding="utf-8-sig")

    assert available_scripts(path) == ["A"]


def test_rebuild_everything_maps_to_force():
    assert "--force" in build_generate_command(force=True)
    assert "--force" not in build_generate_command()


def test_tool_command_uses_script_paths_when_not_packaged():
    command = botc_gui.tool_command("solid_maker")

    assert command[1] == "-u"
    assert command[2].endswith("solid_maker.py")


def test_tool_command_re_runs_the_executable_when_packaged(monkeypatch):
    """A packaged build has no .py files beside it, so it re-runs itself."""
    monkeypatch.setattr(botc_gui, "FROZEN", True)
    monkeypatch.setattr(botc_gui.sys, "executable", "/apps/BOTC.exe")

    assert botc_gui.tool_command("solid_maker") == [
        "/apps/BOTC.exe",
        "--run-tool",
        "solid_maker",
    ]


def test_packaged_dispatch_runs_the_named_tool(monkeypatch):
    called = {}

    class FakeModule:
        def main(self, argv):
            called["argv"] = argv
            return 0

    monkeypatch.setattr(botc_gui.importlib, "import_module", lambda name: FakeModule())
    monkeypatch.setattr(
        botc_gui.sys, "argv", ["BOTC.exe", "--run-tool", "solid_maker", "--skip-stl"]
    )

    with pytest.raises(SystemExit) as excinfo:
        botc_gui.main()

    assert called["argv"] == ["--skip-stl"]  # switches forwarded, not the sentinel
    assert excinfo.value.code == 0


def test_packaged_dispatch_refuses_unknown_modules(monkeypatch):
    imported = []
    monkeypatch.setattr(botc_gui.importlib, "import_module", imported.append)
    monkeypatch.setattr(botc_gui.sys, "argv", ["BOTC.exe", "--run-tool", "os"])

    with pytest.raises(SystemExit) as excinfo:
        botc_gui.main()

    assert imported == []  # never imported, let alone run
    assert "Unknown tool" in str(excinfo.value.code)


def test_every_gui_tool_is_runnable_when_packaged():
    """The allowlist must cover every tool the GUI shells out to."""
    from pathlib import Path

    commands = [
        botc_gui.build_generate_command(),
        botc_gui.build_checklist_command(),
        botc_gui.build_plates_command(),
        botc_gui.build_import_command([]),
        botc_gui.build_contact_sheet_command(),
        botc_gui.build_preflight_command(),
    ]
    # Unpackaged commands are [python, -u, path/to/tool.py, ...].
    used = {Path(command[2]).stem for command in commands}
    assert used <= botc_gui.RUNNABLE_TOOLS


def test_plates_command_defaults_to_every_colour():
    from botc_gui import build_plates_command

    assert build_plates_command()[3:] == []


def test_plates_command_scopes_to_a_script():
    from botc_gui import build_plates_command

    assert build_plates_command(script="Catfishing")[3:] == ["--script", "Catfishing"]


def test_plates_command_passes_art_up():
    from botc_gui import build_plates_command

    assert "--art-up" in build_plates_command(art_up=True)


# -- checklist tab (needs a display, so guarded) ---------------------------
def _make_app(tmp_path, monkeypatch):
    """An App wired to a temp workspace, or None if no display is available."""
    import make_print_checklist as mpc
    import solid_maker as sm

    roles_json = tmp_path / "roles.json"
    roles_json.write_text(
        json.dumps(
            {
                "Imp": {
                    "color": "red",
                    "reminders": ["Dead", "Dead"],
                    "remindersGlobal": [],
                },
                "Chef": {"color": "blue", "reminders": [], "remindersGlobal": []},
            }
        )
    )
    monkeypatch.setattr(sm, "ROLES_JSON", roles_json)
    monkeypatch.setattr(mpc, "ROLES_JSON", roles_json)
    monkeypatch.setattr(mpc, "CUSTOM_ROLES_JSON", tmp_path / "none.json")
    monkeypatch.setattr(mpc, "SCRIPTS_JSON", tmp_path / "none_scripts.json")
    monkeypatch.setattr(mpc, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(mpc, "DEFAULT_OUTPUT", tmp_path / "print_checklist.xlsx")

    try:
        app = botc_gui.App()
    except tk.TclError:
        return None
    app.update()
    return app


def _select_tab(app, name):
    """Select a tab by its label, so adding tabs can't shift the tests."""
    for index in range(app.notebook.index("end")):
        if app.notebook.tab(index, "text") == name:
            app.notebook.select(index)
            app.update()
            return
    raise AssertionError(f"no {name!r} tab")


def test_checklist_tab_loads_rows_and_counts(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    if app is None:
        pytest.skip("no display")
    try:
        _select_tab(app, "Checklist")
        # Imp coin + Imp Dead + Chef coin = 3 token types.
        assert len(app.checklist_rows) == 3
        assert "0 of 3" in app.checklist_summary.cget("text")
    finally:
        app.destroy()


def test_toggle_marks_all_copies_and_persists(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    if app is None:
        pytest.skip("no display")
    try:
        _select_tab(app, "Checklist")
        # Find the Imp Dead reminder row (qty 2) and toggle it printed.
        dead = next(
            iid
            for iid in app.tree.get_children()
            if app._row_for_iid(iid)["reminder"] == "Dead"
        )
        app._set_printed(dead, app._row_for_iid(dead)["qty"])
        key = ("Imp", "Reminder", "Dead")
        assert app.checklist_progress[key] == (2, "")  # both copies
        assert app.checklist_dirty is True
    finally:
        app.destroy()


def test_saving_writes_a_file_the_cli_can_read(tmp_path, monkeypatch):
    import make_print_checklist as mpc

    app = _make_app(tmp_path, monkeypatch)
    if app is None:
        pytest.skip("no display")
    try:
        _select_tab(app, "Checklist")
        app._mark_all()
        app._save_checklist()
        assert app.checklist_output.exists()
        assert app.checklist_dirty is False
        # The CLI must read the GUI's progress back.
        progress = mpc.read_existing_progress(app.checklist_output)
        assert progress[("Chef", "Character", "")][0] == 1
    finally:
        app.destroy()


# -- progress bar ----------------------------------------------------------
def _app_or_skip():
    try:
        app = botc_gui.App()
    except tk.TclError:
        pytest.skip("no display")
    app.update()
    return app


def test_progress_line_switches_bar_to_determinate():
    app = _app_or_skip()
    try:
        app._busy(True)
        assert str(app.progress["mode"]) == "indeterminate"

        handled = app._handle_progress("[progress] 1/8 Imp\n")

        assert handled is True
        assert str(app.progress["mode"]) == "determinate"
        assert int(app.progress["maximum"]) == 8
        assert int(app.progress["value"]) == 1
        assert "1 of 8" in app.status.cget("text")
        assert "Imp" in app.status.cget("text")
    finally:
        app.destroy()


def test_progress_advances_and_resets_when_done():
    app = _app_or_skip()
    try:
        app._busy(True)
        app._handle_progress("[progress] 3/8 Chef\n")
        assert int(app.progress["value"]) == 3

        app._busy(False)  # run finished
        assert int(app.progress["value"]) == 0
    finally:
        app.destroy()


def test_normal_lines_are_not_treated_as_progress():
    app = _app_or_skip()
    try:
        assert app._handle_progress("Converted foo.png to bar.png\n") is False
        assert app._handle_progress("[ok] STL exported: x.stl\n") is False
    finally:
        app.destroy()


def test_plate_command_carries_include_all():
    from botc_gui import build_plates_command

    assert "--all" not in build_plates_command(script="X")
    assert "--all" in build_plates_command(script="X", include_all=True)


def test_reprint_selected_clears_printed(tmp_path, monkeypatch):
    app = _make_app(tmp_path, monkeypatch)
    if app is None:
        pytest.skip("no display")
    try:
        _select_tab(app, "Checklist")
        iid = app.tree.get_children()[0]
        row = app._row_for_iid(iid)
        key = app._mpc.progress_key(row)
        app._set_printed(iid, row["qty"])
        assert app.checklist_progress.get(key) == (row["qty"], "")

        app.tree.selection_set(iid)
        app._reprint_selected()
        assert key not in app.checklist_progress  # cleared -> prints again
    finally:
        app.destroy()


def test_missing_features_is_empty_when_files_match():
    from botc_gui import missing_features

    assert missing_features("solid_maker") == []
    assert missing_features("make_print_checklist") == []


def test_missing_features_names_what_is_absent(monkeypatch):
    import solid_maker as sm
    from botc_gui import missing_features

    monkeypatch.delattr(sm, "effective_settings")

    assert missing_features("solid_maker") == ["effective_settings"]


def test_window_still_opens_when_a_sibling_is_out_of_date(monkeypatch):
    """A partial drop-in used to kill the window with an AttributeError."""
    import solid_maker as sm

    monkeypatch.delattr(sm, "effective_settings")
    monkeypatch.delattr(sm, "save_settings")

    try:
        app = botc_gui.App()
    except tk.TclError:
        pytest.skip("no display")
    try:
        app.update()
        # The Settings tab shows a notice rather than fields...
        assert app.settings_vars == {}
        # ...and the rest of the window is intact.
        tabs = [app.notebook.tab(i, "text") for i in range(app.notebook.index("end"))]
        assert tabs == ["Tokens", "Checklist", "Settings"]
        assert "older than this window" in app.log.get("1.0", "end")
    finally:
        app.destroy()


def test_output_pane_is_shared_not_inside_a_tab():
    """The log must be visible whichever tab started the run."""
    app = _app_or_skip()
    try:
        # Its master chain should reach the window without passing the notebook.
        parents = []
        widget = app.log
        while widget is not None and widget is not app:
            widget = widget.master
            parents.append(widget)
        assert app.notebook not in parents
    finally:
        app.destroy()
