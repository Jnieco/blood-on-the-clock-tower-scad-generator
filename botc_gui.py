"""A small desktop window for generating tokens and the print checklist.

Nothing here reimplements the tools: it builds the same commands you would type
and runs them, streaming their output into the log pane. That keeps one source
of truth, so the GUI cannot drift from the scripts.

    uv run python botc_gui.py

Tkinter ships with Python, so this needs no extra dependencies.
"""

import importlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

# When packaged as a .exe, sys.executable is the .exe and the Python sources are
# not on disk beside it, so the tools are invoked by re-running the .exe with a
# --run-tool switch instead of by script path.
FROZEN = getattr(sys, "frozen", False)
PROJECT_ROOT = (
    Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
)


def tool_command(module_name):
    """The command prefix that runs one of the tools, packaged or not."""
    if FROZEN:
        return [sys.executable, "--run-tool", module_name]
    return [sys.executable, "-u", str(PROJECT_ROOT / f"{module_name}.py")]


SCRIPTS_JSON = PROJECT_ROOT / "scripts.json"
ROLES_JSON = PROJECT_ROOT / "roles.json"
CHECKLIST = PROJECT_ROOT / "print_checklist.xlsx"
THREEMF_DIR = PROJECT_ROOT / "3mf"

ALL_SCRIPTS = "(every role)"


def available_scripts(scripts_json=SCRIPTS_JSON):
    """Script names from scripts.json, or [] if it is missing or unreadable."""
    try:
        with open(scripts_json, encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    return sorted(name for name in data if not name.startswith("_"))


def _script_args(script=None, script_file=None):
    """--script-file or --script for a selection; a file wins, as in the tools."""
    if script_file:
        return ["--script-file", str(script_file)]
    if script and script != ALL_SCRIPTS:
        return ["--script", script]
    return []


def build_generate_command(
    script=None,
    script_file=None,
    only=None,
    skip_stl=False,
    no_reminders=False,
    no_3mf=False,
    art_up=False,
    force=False,
    choose_tokens=False,
):
    """The `solid_maker.py` command for the chosen options."""
    command = tool_command("solid_maker") + _script_args(script, script_file)
    if only:
        command += ["--only", only]
    if skip_stl:
        command.append("--skip-stl")
    if no_reminders:
        command.append("--no-reminders")
    if no_3mf:
        command.append("--no-3mf")
    if art_up:
        command.append("--art-up")
    if force:
        command.append("--force")
    if choose_tokens:
        command.append("--choose-tokens")
    return command


def build_checklist_command(
    script=None, script_file=None, output=None, merge=True, overwrite=False
):
    """The `make_print_checklist.py` command for the chosen options."""
    command = tool_command("make_print_checklist") + _script_args(script, script_file)
    if output:
        command += ["--output", str(output)]
    if merge:
        command.append("--merge")
    if overwrite:
        command.append("--overwrite")
    return command


def build_plates_command(
    script=None, script_file=None, art_up=False, include_all=False, choose_tokens=False
):
    """The `make_plates.py` command for the chosen options."""
    command = tool_command("make_plates") + _script_args(script, script_file)
    if art_up:
        command.append("--art-up")
    if include_all:
        command.append("--all")
    if choose_tokens:
        command.append("--choose-tokens")
    return command


def build_import_command(files):
    """The `import_script.py` command for the chosen files."""
    return tool_command("import_script") + [str(f) for f in files]


def build_contact_sheet_command(script=None, script_file=None):
    """The `make_contact_sheet.py` command for the chosen options."""
    return tool_command("make_contact_sheet") + _script_args(script, script_file)


def build_preflight_command(script=None, script_file=None):
    """The `art_preflight.py` command for the chosen options."""
    return tool_command("art_preflight") + _script_args(script, script_file)


def _format_number(value):
    """Trim trailing zeros so 5.0 shows as '5' and 0.30 as '0.3'."""
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


# The GUI is a launcher over the other scripts, so it can be newer than the file
# beside it if only some were copied in. Rather than die with an AttributeError
# deep inside a tab, check up front and say which file to update.
REQUIRED_FEATURES = {
    "solid_maker": (
        "TUNABLE_SETTINGS",
        "SETTINGS_JSON",
        "SETTINGS_COLOUR_KEY",
        "effective_settings",
        "load_settings",
        "save_settings",
        "validate_setting",
    ),
    "make_print_checklist": (
        "DEFAULT_OUTPUT",
        "load_inventory",
        "progress_key",
        "read_existing_progress",
        "write_checklist",
    ),
}


def missing_features(module_name):
    """Names this GUI needs from `module_name` that are not there.

    An empty list means the file beside us is new enough.
    """
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return list(REQUIRED_FEATURES.get(module_name, ()))
    return [
        name
        for name in REQUIRED_FEATURES.get(module_name, ())
        if not hasattr(module, name)
    ]


def _out_of_date_message(module_name, missing):
    return (
        f"{module_name}.py is older than this window and is missing "
        f"{len(missing)} thing(s) it needs ({', '.join(missing[:3])}"
        f"{'...' if len(missing) > 3 else ''}).\n\n"
        f"Copy the latest {module_name}.py into this folder and reopen the "
        "window. Everything else keeps working in the meantime."
    )


def _refresh_script_box(box, variable):
    """Reload a script dropdown, keeping the choice if it still exists."""
    choices = [ALL_SCRIPTS, *available_scripts()]
    box["values"] = choices
    if variable.get() not in choices:
        variable.set(ALL_SCRIPTS)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Blood on the Clocktower - token generator")
        self.geometry("780x680")
        self.minsize(700, 600)

        self.script_file = None
        self.output_queue = queue.Queue()
        self.process = None

        self._build_widgets()
        self._refresh_scripts()
        self.after(100, self._drain_output)

    # -- layout ----------------------------------------------------------
    def _build_widgets(self):
        padding = {"padx": 8, "pady": 4}

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=6, pady=(6, 0))

        generate_tab = ttk.Frame(self.notebook)
        checklist_tab = ttk.Frame(self.notebook)
        settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(generate_tab, text="Tokens")
        self.notebook.add(checklist_tab, text="Checklist")
        self.notebook.add(settings_tab, text="Settings")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        source = ttk.LabelFrame(generate_tab, text="1. What to make")
        source.pack(fill="x", **padding)

        row = ttk.Frame(source)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Script:").pack(side="left")
        self.script_var = tk.StringVar(value=ALL_SCRIPTS)
        self.script_box = ttk.Combobox(
            row, textvariable=self.script_var, state="readonly", width=32
        )
        self.script_box.pack(side="left", padx=6)
        ttk.Button(row, text="Refresh", command=self._refresh_scripts).pack(side="left")
        ttk.Button(row, text="Import script file...", command=self._import_script).pack(
            side="left", padx=6
        )

        row = ttk.Frame(source)
        row.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Button(
            row, text="Use a script file directly...", command=self._choose_script_file
        ).pack(side="left")
        self.script_file_label = ttk.Label(row, text="(none)", foreground="#666")
        self.script_file_label.pack(side="left", padx=6)
        ttk.Button(row, text="Clear", command=self._clear_script_file).pack(side="left")

        row = ttk.Frame(source)
        row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(row, text="Single role (optional):").pack(side="left")
        self.only_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.only_var, width=24).pack(side="left", padx=6)

        options = ttk.LabelFrame(generate_tab, text="2. Options")
        options.pack(fill="x", **padding)
        grid = ttk.Frame(options)
        grid.pack(fill="x", padx=8, pady=6)
        self.skip_stl = tk.BooleanVar()
        self.no_reminders = tk.BooleanVar()
        self.no_3mf = tk.BooleanVar()
        self.art_up = tk.BooleanVar()
        self.force = tk.BooleanVar()
        self.plate_all = tk.BooleanVar()
        self.choose_tokens = tk.BooleanVar()
        checks = [
            ("Preview only, no STL/3MF (fast)", self.skip_stl),
            ("Character tokens only", self.no_reminders),
            ("Skip 3MF (STL only)", self.no_3mf),
            ("Art faces up (default is down)", self.art_up),
            ("Rebuild everything (ignore unchanged)", self.force),
            ("Plate all tokens (ignore printed)", self.plate_all),
            ('Also "Choose a..." draft tokens', self.choose_tokens),
        ]
        for index, (label, var) in enumerate(checks):
            ttk.Checkbutton(grid, text=label, variable=var).grid(
                row=index // 2, column=index % 2, sticky="w", padx=4, pady=2
            )

        actions = ttk.LabelFrame(generate_tab, text="3. Run")
        actions.pack(fill="x", **padding)
        row = ttk.Frame(actions)
        row.pack(fill="x", padx=8, pady=8)
        self.generate_button = ttk.Button(
            row, text="Generate tokens", command=self._generate
        )
        self.generate_button.pack(side="left")
        self.checklist_button = ttk.Button(
            row, text="Update checklist", command=self._checklist
        )
        self.checklist_button.pack(side="left", padx=6)
        self.plates_button = ttk.Button(row, text="Make plates", command=self._plates)
        self.plates_button.pack(side="left")
        self.contact_button = ttk.Button(
            row, text="Contact sheet", command=self._contact_sheet
        )
        self.contact_button.pack(side="left", padx=6)
        self.preflight_button = ttk.Button(
            row, text="Check art", command=self._preflight
        )
        self.preflight_button.pack(side="left")
        self.stop_button = ttk.Button(
            row, text="Stop", command=self._stop, state="disabled"
        )
        self.stop_button.pack(side="left")
        ttk.Button(row, text="Open plates folder", command=self._open_plates).pack(
            side="right"
        )
        ttk.Button(row, text="Open 3mf folder", command=self._open_3mf).pack(
            side="right", padx=6
        )
        ttk.Button(row, text="Open checklist", command=self._open_checklist).pack(
            side="right", padx=6
        )

        self._build_checklist_tab(checklist_tab)
        self._build_settings_tab(settings_tab)

        # The output pane lives below the notebook rather than inside a tab, so
        # you can watch a run from whichever tab started it.
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=14, pady=(4, 0))

        log_frame = ttk.LabelFrame(self, text="Output")
        log_frame.pack(fill="both", expand=False, padx=10, pady=4)
        self.log = scrolledtext.ScrolledText(
            log_frame, height=9, wrap="word", state="disabled"
        )
        self.log.pack(fill="both", expand=True, padx=6, pady=6)
        self.log.tag_configure("warn", foreground="#b06000")
        self.log.tag_configure("error", foreground="#a00000")
        self.log.tag_configure("done", foreground="#006000")

        self.status = ttk.Label(self, text="Ready", anchor="w")
        self.status.pack(fill="x", padx=10, pady=(0, 8))

        if not ROLES_JSON.exists():
            self._write(
                "roles.json not found. Run get_all_roles.py first, or import a "
                "script file that defines its own characters.\n",
                "warn",
            )
        for module_name in REQUIRED_FEATURES:
            missing = missing_features(module_name)
            if missing:
                self._write(
                    f"{module_name}.py is older than this window; some tabs are "
                    f"disabled. Copy the latest {module_name}.py into this "
                    "folder and reopen.\n",
                    "warn",
                )

    # -- checklist tab ---------------------------------------------------
    def _build_checklist_tab(self, parent):
        import make_print_checklist as mpc

        self._mpc = mpc
        self.checklist_rows = []  # list of row dicts from build_inventory
        self.checklist_progress = {}  # (role,type,reminder) -> (printed, notes)
        self.checklist_output = None  # which .xlsx this maps to
        self.checklist_dirty = False

        missing = missing_features("make_print_checklist")
        if missing:
            ttk.Label(
                parent,
                text=_out_of_date_message("make_print_checklist", missing),
                wraplength=700,
                justify="left",
                foreground="#a00000",
            ).pack(anchor="w", padx=12, pady=12)
            return

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Show:").pack(side="left")
        self.filter_script = tk.StringVar(value=ALL_SCRIPTS)
        self.filter_box = ttk.Combobox(
            top, textvariable=self.filter_script, state="readonly", width=26
        )
        self.filter_box.pack(side="left", padx=6)
        self.filter_box.bind(
            "<<ComboboxSelected>>", lambda _e: self._reload_checklist()
        )

        self.hide_done = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            top,
            text="Unprinted only",
            variable=self.hide_done,
            command=self._refill_checklist,
        ).pack(side="left", padx=8)
        ttk.Button(top, text="Reload", command=self._reload_checklist).pack(side="left")
        ttk.Button(top, text="Save to Excel", command=self._save_checklist).pack(
            side="right"
        )

        columns = ("printed", "role", "type", "reminder", "team", "need", "have")
        headings = {
            "printed": "Done",
            "role": "Role",
            "type": "Type",
            "reminder": "Reminder",
            "team": "Colour",
            "need": "Need",
            "have": "Printed",
        }
        widths = {
            "printed": 44,
            "role": 150,
            "type": 80,
            "reminder": 150,
            "team": 70,
            "need": 48,
            "have": 56,
        }
        table = ttk.Frame(parent)
        table.pack(fill="both", expand=True, padx=8, pady=(0, 4))
        self.tree = ttk.Treeview(
            table, columns=columns, show="headings", selectmode="browse"
        )
        for col in columns:
            self.tree.heading(col, text=headings[col])
            anchor = "w" if col in ("role", "reminder") else "center"
            self.tree.column(col, width=widths[col], anchor=anchor, stretch=False)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("done", background="#e4f5e4")

        # Click the Done cell to toggle; double-click a row to set an exact count.
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double)
        # Right-click (and Control-click on macOS) opens a small action menu.
        self.tree.bind("<Button-3>", self._on_tree_menu)
        self.tree.bind("<Control-Button-1>", self._on_tree_menu)
        self.row_menu = tk.Menu(self.tree, tearoff=0)
        self.row_menu.add_command(
            label="Mark printed", command=lambda: self._menu_set(True)
        )
        self.row_menu.add_command(
            label="Mark for reprint (clear printed)",
            command=lambda: self._menu_set(False),
        )

        bottom = ttk.Frame(parent)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        self.checklist_summary = ttk.Label(bottom, text="No checklist loaded.")
        self.checklist_summary.pack(side="left")
        ttk.Button(bottom, text="Mark all shown printed", command=self._mark_all).pack(
            side="right"
        )
        ttk.Button(
            bottom, text="Reprint selected", command=self._reprint_selected
        ).pack(side="right", padx=6)
        ttk.Button(bottom, text="Add reminder...", command=self._add_reminder).pack(
            side="right", padx=6
        )

    def _on_tab_changed(self, _event):
        tab = self.notebook.tab(self.notebook.select(), "text")
        if tab == "Checklist":
            if not hasattr(self, "tree"):
                return  # tab showed an out-of-date notice instead
            self._refresh_filter_scripts()
            if not self.checklist_rows:
                self._reload_checklist()

    def _refresh_filter_scripts(self):
        _refresh_script_box(self.filter_box, self.filter_script)

    def _reload_checklist(self):
        """Rebuild the row list for the current filter and load saved progress."""
        if self.checklist_dirty and not messagebox.askyesno(
            "Unsaved changes",
            "You have changes that are not saved to Excel yet. Reload and lose them?",
        ):
            return
        script = self.filter_script.get()
        try:
            rows, output = self._mpc.load_inventory(
                script=None if script == ALL_SCRIPTS else script
            )
        except Exception as error:  # noqa: BLE001 - surfaced to the user
            self.checklist_summary.config(text=f"Could not load: {error}")
            self.checklist_rows = []
            self._refill_checklist()
            return

        self.checklist_rows = rows
        self.checklist_output = output
        # Seed from the file this selection saves to, falling back to the master.
        source = output if output.exists() else self._mpc.DEFAULT_OUTPUT
        self.checklist_progress = self._mpc.read_existing_progress(source)
        self.checklist_dirty = False
        self._refill_checklist()

    def _refill_checklist(self):
        self.tree.delete(*self.tree.get_children())
        if not self.checklist_rows:
            self.checklist_summary.config(text="No checklist loaded.")
            return
        done_tokens = 0
        shown = 0
        for index, row in enumerate(self.checklist_rows):
            key = self._mpc.progress_key(row)
            printed, _notes = self.checklist_progress.get(key, (0, ""))
            complete = printed >= row["qty"]
            if complete:
                done_tokens += 1
            if self.hide_done.get() and complete:
                continue
            shown += 1
            mark = "X" if complete else ("~" if printed else "")
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    mark,
                    row["role"],
                    row["type"],
                    row["reminder"] or "-",
                    row["team"],
                    row["qty"],
                    printed,
                ),
                tags=("done",) if complete else (),
            )
        total = len(self.checklist_rows)
        where = self.checklist_output.name if self.checklist_output else "-"
        self.checklist_summary.config(
            text=(
                f"{done_tokens} of {total} token types printed"
                f"{f'  -  showing {shown}' if shown != total else ''}   ->  {where}"
                f"{'   (unsaved)' if self.checklist_dirty else ''}"
            )
        )

    def _row_for_iid(self, iid):
        return self.checklist_rows[int(iid)]

    def _set_printed(self, iid, printed):
        row = self._row_for_iid(iid)
        key = self._mpc.progress_key(row)
        _old, notes = self.checklist_progress.get(key, (0, ""))
        printed = max(0, int(printed))
        if printed or notes:
            self.checklist_progress[key] = (printed, notes)
        else:
            self.checklist_progress.pop(key, None)
        self.checklist_dirty = True
        self._refill_checklist()

    def _on_tree_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":  # only the Done column toggles
            return
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        row = self._row_for_iid(iid)
        key = self._mpc.progress_key(row)
        printed, _ = self.checklist_progress.get(key, (0, ""))
        # Toggle: if fully printed, clear; otherwise mark all copies printed.
        self._set_printed(iid, 0 if printed >= row["qty"] else row["qty"])

    def _on_tree_double(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        row = self._row_for_iid(iid)
        if row["qty"] == 1:
            return  # a single-copy token is just the toggle
        key = self._mpc.progress_key(row)
        printed, _ = self.checklist_progress.get(key, (0, ""))
        answer = simpledialog.askinteger(
            "How many printed?",
            f"{row['role']} - {row['reminder']}\nYou need {row['qty']} of these.",
            initialvalue=printed,
            minvalue=0,
            maxvalue=row["qty"],
            parent=self,
        )
        if answer is not None:
            self._set_printed(iid, answer)

    def _on_tree_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self._menu_iid = iid
        self.row_menu.tk_popup(event.x_root, event.y_root)

    def _menu_set(self, printed):
        iid = getattr(self, "_menu_iid", None)
        if iid is None:
            return
        row = self._row_for_iid(iid)
        self._set_printed(iid, row["qty"] if printed else 0)

    def _reprint_selected(self):
        """Clear the printed count for the selected row so it prints again."""
        selection = self.tree.selection()
        if not selection:
            self.checklist_summary.config(text="Select a row to mark for reprint.")
            return
        for iid in selection:
            self._set_printed(iid, 0)

    def _add_reminder(self):
        """Add a reminder to a role, saved to extra_reminders.json.

        Prefills the role from the selected row, but the role is editable so you
        can add one to anything -- including a role that has no tokens showing
        yet. The text is free-form.
        """
        import solid_maker as sm

        default_role = ""
        selection = self.tree.selection()
        if selection:
            default_role = self._row_for_iid(selection[0])["role"]

        role = simpledialog.askstring(
            "Add reminder",
            "Which role is this reminder for?",
            initialvalue=default_role,
            parent=self,
        )
        if not role or not role.strip():
            return
        label = simpledialog.askstring(
            "Add reminder",
            f"Reminder text to add to {role.strip()}:",
            parent=self,
        )
        if not label or not label.strip():
            return

        try:
            added = sm.add_extra_reminder(role.strip(), label.strip())
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not add reminder", str(error))
            return

        if added:
            self.checklist_summary.config(
                text=f"Added reminder {label.strip()!r} to {role.strip()}. "
                "Reload to see it, then Generate."
            )
            self._reload_checklist()
        else:
            self.checklist_summary.config(
                text=f"{role.strip()} already has a {label.strip()!r} reminder."
            )

    def _mark_all(self):
        for iid in self.tree.get_children():
            row = self._row_for_iid(iid)
            key = self._mpc.progress_key(row)
            _old, notes = self.checklist_progress.get(key, (0, ""))
            self.checklist_progress[key] = (row["qty"], notes)
        self.checklist_dirty = True
        self._refill_checklist()

    def _save_checklist(self):
        if not self.checklist_rows or not self.checklist_output:
            return
        try:
            self._mpc.write_checklist(
                self.checklist_rows, self.checklist_output, self.checklist_progress
            )
        except Exception as error:  # noqa: BLE001 - surfaced to the user
            messagebox.showerror("Could not save", str(error))
            return
        self.checklist_dirty = False
        self._refill_checklist()
        self.status.config(text=f"Saved {self.checklist_output.name}")

    # -- settings tab ----------------------------------------------------
    def _build_settings_tab(self, parent):
        import solid_maker as sm

        self._sm = sm
        self.settings_vars = {}
        missing = missing_features("solid_maker")
        if missing:
            # An older solid_maker.py can't be tuned from here; say so plainly
            # instead of failing with an AttributeError and taking the window
            # down with it.
            ttk.Label(
                parent,
                text=_out_of_date_message("solid_maker", missing),
                wraplength=700,
                justify="left",
                foreground="#a00000",
            ).pack(anchor="w", padx=12, pady=12)
            return
        # Friendly labels and groups for the tunables worth exposing. Anything
        # in TUNABLE_SETTINGS not listed here still saves at its current value;
        # this just decides what gets a field.
        groups = [
            (
                "Character token",
                [
                    ("COIN_DIAMETER", "Disc diameter (mm)"),
                    ("TEXT_SIZE", "Role name size (mm)"),
                    ("ROLE_ICON_DIAMETER", "Art size cap (mm)"),
                    ("ROLE_ICON_SHIFT_Y", "Art lift above name (mm)"),
                    ("ROLE_EXTRUDE_DEPTH", "Colour inlay depth (mm)"),
                ],
            ),
            (
                "Reminder token",
                [
                    ("REMINDER_DIAMETER", "Disc diameter (mm)"),
                    ("REMINDER_TEXT_SIZE", "Text size (mm)"),
                    ("REMINDER_ICON_MAX_FRACTION", "Max art fraction (0-1)"),
                    ("REMINDER_ICON_DILATE", "Fatten thin art (mm)"),
                ],
            ),
            (
                "Art warnings",
                [("ART_MIN_FEATURE_MM", "Smallest safe detail (mm)")],
            ),
        ]

        header = ttk.Frame(parent)
        header.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(
            header,
            text=(
                "Sizes are in millimetres. Blank a field to use the built-in "
                "default. Changes apply on the next Generate."
            ),
            wraplength=720,
            foreground="#444",
        ).pack(side="left")

        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True, padx=8, pady=4)

        current = sm.effective_settings()
        self.settings_vars = {}
        row = 0
        for group_name, entries in groups:
            label = ttk.Label(body, text=group_name, font=("TkDefaultFont", 10, "bold"))
            label.grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
            row += 1
            for key, human in entries:
                ttk.Label(body, text=human).grid(
                    row=row, column=0, sticky="w", padx=(12, 6), pady=1
                )
                var = tk.StringVar(value=_format_number(current.get(key)))
                entry = ttk.Entry(body, textvariable=var, width=12)
                entry.grid(row=row, column=1, sticky="w", pady=1)
                _kind, low, high = sm.TUNABLE_SETTINGS[key]
                if low is not None:
                    ttk.Label(
                        body,
                        text=f"({_format_number(low)}-{_format_number(high)})",
                        foreground="#888",
                    ).grid(row=row, column=2, sticky="w", padx=6)
                self.settings_vars[key] = var
                row += 1

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=8, pady=8)
        ttk.Button(buttons, text="Save settings", command=self._save_settings).pack(
            side="left"
        )
        ttk.Button(
            buttons, text="Reset to defaults", command=self._reset_settings
        ).pack(side="left", padx=6)
        self.settings_status = ttk.Label(buttons, text="")
        self.settings_status.pack(side="left", padx=10)

    def _save_settings(self):
        sm = self._sm
        data = {}
        problems = []
        for key, var in self.settings_vars.items():
            text = var.get().strip()
            if not text:
                continue  # blank -> fall back to the built-in default
            try:
                value = float(text)
            except ValueError:
                problems.append(f"{key}: '{text}' is not a number")
                continue
            kind = sm.TUNABLE_SETTINGS[key][0]
            value = kind(value)
            problem = sm.validate_setting(key, value)
            if problem:
                problems.append(problem)
            else:
                data[key] = value

        if problems:
            self.settings_status.config(
                text="  ".join(problems[:2]) + ("  ..." if len(problems) > 2 else ""),
                foreground="#a00000",
            )
            return

        # Preserve any keys the tab doesn't expose (e.g. custom team colours).
        try:
            existing = sm.load_settings()
        except ValueError:
            existing = {}
        for key in list(existing):
            if key in sm.TUNABLE_SETTINGS and key not in self.settings_vars:
                data[key] = existing[key]
        if sm.SETTINGS_COLOUR_KEY in existing:
            data[sm.SETTINGS_COLOUR_KEY] = existing[sm.SETTINGS_COLOUR_KEY]

        try:
            sm.save_settings(data)
        except OSError as error:
            self.settings_status.config(text=str(error), foreground="#a00000")
            return
        self.settings_status.config(
            text=f"Saved {sm.SETTINGS_JSON.name}. Applies on the next Generate.",
            foreground="#006000",
        )

    def _reset_settings(self):
        if not messagebox.askyesno(
            "Reset settings",
            "Clear settings.json so every size falls back to the built-in "
            "default on the next run? This cannot be undone.",
        ):
            return
        sm = self._sm
        try:
            if sm.SETTINGS_JSON.exists():
                sm.SETTINGS_JSON.unlink()
        except OSError as error:
            self.settings_status.config(text=str(error), foreground="#a00000")
            return
        # The fields show the values in effect this session; the built-in
        # defaults take over fully on the next launch.
        for key, var in self.settings_vars.items():
            var.set(_format_number(getattr(sm, key)))
        self.settings_status.config(
            text="Cleared settings.json - defaults apply on the next run.",
            foreground="#006000",
        )

    # -- helpers ---------------------------------------------------------
    def _refresh_scripts(self):
        _refresh_script_box(self.script_box, self.script_var)

    def _choose_script_file(self):
        path = filedialog.askopenfilename(
            title="Choose a script .json from the script tool",
            filetypes=[("Script JSON", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.script_file = Path(path)
            self.script_file_label.config(text=self.script_file.name, foreground="#000")

    def _clear_script_file(self):
        self.script_file = None
        self.script_file_label.config(text="(none)", foreground="#666")

    def _import_script(self):
        paths = filedialog.askopenfilenames(
            title="Import script .json file(s)",
            filetypes=[("Script JSON", "*.json"), ("All files", "*.*")],
        )
        if paths:
            self._run(build_import_command(paths), after=self._refresh_scripts)

    def _write(self, text, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", text, tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_path(self, path):
        path = Path(path)
        if not path.exists():
            messagebox.showinfo("Not there yet", f"{path.name} does not exist yet.")
            return
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    def _open_3mf(self):
        self._open_path(THREEMF_DIR)

    def _open_plates(self):
        self._open_path(PROJECT_ROOT / "plates")

    def _open_checklist(self):
        self._open_path(CHECKLIST)

    # -- running ---------------------------------------------------------
    def _busy(self, running):
        state = "disabled" if running else "normal"
        for button in (
            self.generate_button,
            self.checklist_button,
            self.plates_button,
            self.contact_button,
            self.preflight_button,
        ):
            button.config(state=state)
        self.stop_button.config(state="normal" if running else "disabled")
        if running:
            # Start indeterminate; the first [progress] line switches it to a
            # real bar. Some runs (checklist, import) never report progress and
            # stay as a moving barber-pole, which is the right signal for them.
            self.progress.config(mode="indeterminate")
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.config(mode="determinate", value=0)

    def _run(self, command, after=None):
        if self.process is not None:
            messagebox.showinfo("Busy", "Something is already running.")
            return
        self._write(f"\n> {' '.join(command[1:])}\n")
        self.status.config(text="Working...")
        self._busy(True)

        def worker():
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=str(PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                for line in self.process.stdout:
                    self.output_queue.put(("line", line))
                code = self.process.wait()
            except OSError as error:
                self.output_queue.put(("line", f"Could not run: {error}\n"))
                code = 1
            finally:
                self.process = None
            self.output_queue.put(("done", code))
            if after is not None:
                self.output_queue.put(("after", after))

        threading.Thread(target=worker, daemon=True).start()

    def _handle_progress(self, text):
        """Update the bar from a '[progress] N/M label' line. True if handled."""
        match = re.match(r"\[progress\]\s+(\d+)/(\d+)\s*(.*)", text.strip())
        if not match:
            return False
        done, total, label = int(match[1]), int(match[2]), match[3].strip()
        if self.progress["mode"] != "determinate":
            self.progress.stop()
            self.progress.config(mode="determinate", maximum=total, value=0)
        self.progress.config(maximum=total, value=done)
        self.status.config(
            text=f"{done} of {total}: {label}" if label else f"{done} of {total}"
        )
        return True

    def _drain_output(self):
        """Move worker output onto the UI thread; Tk is not thread-safe."""
        try:
            while True:
                kind, payload = self.output_queue.get_nowait()
                if kind == "line":
                    # Progress lines drive the bar instead of filling the log.
                    if self._handle_progress(payload):
                        continue
                    tag = None
                    stripped = payload.strip()
                    if stripped.startswith("!") or " ! " in payload:
                        tag = "warn"
                    elif "error:" in payload.lower() or "Traceback" in payload:
                        tag = "error"
                    self._write(payload, tag)
                elif kind == "after":
                    payload()
                else:
                    self._busy(False)
                    if payload == 0:
                        self._write("Finished.\n", "done")
                        self.status.config(text="Done")
                    else:
                        self._write(f"Failed (exit code {payload}).\n", "error")
                        self.status.config(text="Failed - see output above")
        except queue.Empty:
            pass
        self.after(100, self._drain_output)

    def _stop(self):
        if self.process is not None:
            self.process.terminate()
            self._write("Stopped.\n", "warn")

    def _generate(self):
        self._run(
            build_generate_command(
                script=self.script_var.get(),
                script_file=self.script_file,
                only=self.only_var.get().strip() or None,
                skip_stl=self.skip_stl.get(),
                no_reminders=self.no_reminders.get(),
                no_3mf=self.no_3mf.get(),
                art_up=self.art_up.get(),
                force=self.force.get(),
                choose_tokens=self.choose_tokens.get(),
            )
        )

    def _contact_sheet(self):
        self._run(
            build_contact_sheet_command(
                script=self.script_var.get(), script_file=self.script_file
            )
        )

    def _preflight(self):
        self._run(
            build_preflight_command(
                script=self.script_var.get(), script_file=self.script_file
            )
        )

    def _plates(self):
        # Plates are built from the token STLs, so make sure they exist first.
        script = self.script_var.get()
        if not (PROJECT_ROOT / "stls").exists():
            self._write(
                "No tokens generated yet - press Generate tokens first.\n", "warn"
            )
            return
        self._run(
            build_plates_command(
                script=script,
                script_file=self.script_file,
                art_up=self.art_up.get(),
                include_all=self.plate_all.get(),
                choose_tokens=self.choose_tokens.get(),
            )
        )

    def _checklist(self):
        # Always merge, so using the GUI can never quietly discard ticks. When
        # narrowing to a script, write a separate sheet seeded from the master
        # rather than overwriting it.
        script = self.script_var.get()
        scoped = bool(self.script_file) or script != ALL_SCRIPTS
        if scoped:
            name = self.script_file.stem if self.script_file else script
            # Name it exactly as the tools do, so the Checklist tab and plates
            # both find this sheet again ("Lil' Monsta" -> Lil_Monsta).
            output = PROJECT_ROOT / f"checklist_{self._sm.safe_name(name)}.xlsx"
            command = build_checklist_command(
                script=script, script_file=self.script_file, output=output, merge=False
            )
            if CHECKLIST.exists():
                command += ["--merge-from", str(CHECKLIST)]
            self._write(
                f"Writing a separate sheet for this script "
                f"({output.name}); {CHECKLIST.name} is left as it is.\n"
            )
        else:
            command = build_checklist_command(merge=CHECKLIST.exists())
        self._run(command)


def main():
    # Packaged builds re-run themselves to execute a tool; see tool_command().
    if len(sys.argv) > 2 and sys.argv[1] == "--run-tool":
        module = importlib.import_module(sys.argv[2])
        sys.exit(module.main(sys.argv[3:]) or 0)
    App().mainloop()


if __name__ == "__main__":
    main()
