"""Build an Excel checklist of every token this project can generate.

One row per physical token (character tokens and reminder tokens), so you can
tick off what you have printed. The inventory is derived from the same role
data and the same filename rules the generator uses, so the "File" column
matches what actually lands in 3mf/.

    uv run python make_print_checklist.py
    uv run python make_print_checklist.py --script "Trouble Brewing"
    uv run python make_print_checklist.py --output my_progress.xlsx

Re-running rebuilds the sheet. To keep the ticks you have already made, pass
--merge, which reads the printed quantities and notes out of the existing file
first.
"""

import argparse
from collections import Counter
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from solid_maker import (
    CUSTOM_ROLES_JSON,
    PROJECT_ROOT,
    ROLES_JSON,
    SCRIPTS_JSON,
    filter_roles_by_script,
    load_role_definitions,
    load_scripts,
    load_selected_roles,
    parse_script_file,
    role_reminders,
    safe_name,
    script_for_role,
)

DEFAULT_OUTPUT = PROJECT_ROOT / "print_checklist.xlsx"

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="2E2E2E")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")  # cells you fill in
DONE_FILL = PatternFill("solid", fgColor="C6EFCE")
TITLE_FONT = Font(name=FONT, size=14, bold=True)
HEADER_FONT = Font(name=FONT, size=11, bold=True, color="FFFFFF")
BODY_FONT = Font(name=FONT, size=11)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLUMNS = [
    ("Script", 22),
    ("Role", 20),
    ("Source", 10),
    ("Team", 10),
    ("Token type", 12),
    ("Reminder text", 24),
    ("Qty needed", 11),
    ("Qty printed", 11),
    ("Status", 12),
    ("File (3mf)", 42),
    ("Notes", 30),
]
# 1-based column numbers, as openpyxl counts them.
ROLE_COL = 2
TYPE_COL = 5
REMINDER_COL = 6
QTY_NEEDED_COL = 7
QTY_PRINTED_COL = 8
STATUS_COL = 9
NOTES_COL = 11


def build_inventory(roles, scripts):
    """One dict per physical token, mirroring what the generator emits."""
    rows = []
    for role, data in sorted(roles.items(), key=lambda kv: kv[0].lower()):
        colour = data.get("color", "unknown")
        colour_safe = safe_name(colour)
        role_safe = safe_name(role)
        # Fields every token of this role shares. The sheet's "Team" column
        # shows the colour, which is what you load in the slicer; the actual
        # team (townsfolk/outsider/...) is kept too, for anything that groups
        # by team.
        shared = {
            "script": script_for_role(role, scripts),
            "role": role,
            "source": "Custom" if data.get("_source") == "custom" else "Official",
            "team": colour,
            "team_name": str(data.get("team", "") or "").lower(),
        }
        rows.append(
            {
                **shared,
                "type": "Character",
                "reminder": "",
                "qty": 1,
                "file": f"3mf/{colour_safe}_{role_safe}_coin.3mf",
            }
        )
        # A role can list the same reminder twice (you need two of that token),
        # so collapse duplicates into a quantity rather than repeating the row.
        for label, count in Counter(role_reminders(data)).items():
            reminder_safe = safe_name(label)
            rows.append(
                {
                    **shared,
                    "type": "Reminder",
                    "reminder": label,
                    "qty": count,
                    "file": (
                        f"3mf/{colour_safe}_{role_safe}_{reminder_safe}_reminder.3mf"
                    ),
                }
            )
    return rows


def _token_sheet_rows(path):
    """Yield (progress_key, row_values) for each token row of a saved workbook.

    The Tokens sheet's layout (title row, header row, then data) is fixed by
    write_tokens_sheet; this is the single place that reads it back.
    """
    if not Path(path).exists():
        return
    workbook = load_workbook(path, data_only=True)
    if "Tokens" not in workbook.sheetnames:
        return
    for row in workbook["Tokens"].iter_rows(min_row=3, values_only=True):
        if not row or not row[ROLE_COL - 1]:
            continue
        key = (
            str(row[ROLE_COL - 1]),
            str(row[TYPE_COL - 1]),
            str(row[REMINDER_COL - 1] or ""),
        )
        yield key, row


def read_quantity_overrides(path):
    """Pull {(role, type, reminder): qty_needed} out of an existing workbook.

    'Qty needed' is generated from the role data, but it is genuinely yours to
    edit: a Legion script wants twelve Legion tokens, and only you know that.
    Reading it back means those edits survive a rebuild instead of silently
    reverting.
    """
    overrides = {}
    for key, row in _token_sheet_rows(path):
        qty = row[QTY_NEEDED_COL - 1]
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        if qty > 0:
            overrides[key] = qty
    return overrides


def apply_quantity_overrides(rows, overrides):
    """Replace generated quantities with the ones recorded in the workbook.

    Applied before anything reads `qty`, so the checklist and plates both
    agree on how many of each token you actually want.
    """
    if not overrides:
        return rows
    for row in rows:
        qty = overrides.get(progress_key(row))
        if qty:
            row["qty"] = qty
    return rows


def checklist_path_for(script=None, script_file=None):
    """Where the checklist for this selection lives on disk.

    Mirrors the output rules: a scoped run uses checklist_<script>.xlsx, an
    unscoped one uses the master print_checklist.xlsx.
    """
    if script_file:
        name, _, _, _ = parse_script_file(script_file)
        return scoped_checklist_path(name)
    if script:
        return scoped_checklist_path(script)
    return DEFAULT_OUTPUT


def scoped_checklist_path(script_name):
    """checklist_<script>.xlsx: a scoped run's own sheet, never the master."""
    return PROJECT_ROOT / f"checklist_{safe_name(script_name)}.xlsx"


def printed_token_keys(rows, progress):
    """Keys of tokens that are fully printed (qty_printed >= qty_needed).

    A partly-printed reminder (2 of 3) is deliberately NOT included: it still
    needs copies, so it should keep appearing until it is finished.
    """
    printed = set()
    for row in rows:
        have, _notes = progress.get(progress_key(row), (0, ""))
        try:
            have = int(have)
        except (TypeError, ValueError):
            have = 0
        if have >= row["qty"]:
            printed.add(progress_key(row))
    return printed


def read_printed_set(path, rows):
    """Convenience: the fully-printed keys recorded in the workbook at `path`."""
    return printed_token_keys(rows, read_existing_progress(path))


def progress_key(row):
    """The identity a token keeps across rebuilds: (role, type, reminder).

    Used to carry Qty printed and Notes from one workbook to the next, and by
    the GUI to read and write the same rows.
    """
    return (row["role"], row["type"], row["reminder"])


def load_inventory(
    roles_json=None,
    custom_files=None,
    script=None,
    script_file=None,
    scripts_json=None,
):
    """Return (rows, output_path) for the current data, applying any script.

    Shared by the CLI and the GUI so both see exactly the same token list. The
    output path is where progress for this selection lives on disk. Paths default
    to the module-level locations, resolved when called (not at import), so the
    GUI and tests can point them elsewhere.
    """
    if custom_files is None:
        custom_files = [CUSTOM_ROLES_JSON] if CUSTOM_ROLES_JSON.exists() else []
    roles, scripts = load_selected_roles(
        custom_files,
        script=script,
        script_file=script_file,
        roles_json=ROLES_JSON if roles_json is None else roles_json,
        scripts_json=SCRIPTS_JSON if scripts_json is None else scripts_json,
    )
    output = checklist_path_for(script=script, script_file=script_file)
    rows = build_inventory(roles, scripts)
    # Honour any quantities you edited in the workbook for this selection, so
    # every tool sees the counts you actually want.
    apply_quantity_overrides(rows, read_quantity_overrides(output))
    return rows, output


def write_checklist(rows, output, progress, scripts=None):
    """Write a workbook from rows + progress. Returns the row count."""
    workbook = Workbook()
    tokens = workbook.active
    last_row = write_tokens_sheet(tokens, rows, progress)
    write_summary_sheet(workbook.create_sheet(), rows, last_row, scripts or {})
    write_howto_sheet(workbook.create_sheet())
    workbook.save(output)
    return len(rows)


def read_existing_progress(path):
    """Pull {(role, type, reminder): (qty_printed, notes)} out of an old file."""
    progress = {}
    for key, row in _token_sheet_rows(path):
        printed = row[QTY_PRINTED_COL - 1]
        notes = row[NOTES_COL - 1]
        if printed or notes:
            progress[key] = (printed or 0, notes or "")
    return progress


def write_tokens_sheet(sheet, rows, progress):
    sheet.title = "Tokens"
    sheet["A1"] = "Blood on the Clocktower - token print checklist"
    sheet["A1"].font = TITLE_FONT
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
    sheet["G1"] = "Shaded columns are yours: Qty needed, Qty printed and Notes."
    sheet["G1"].font = Font(name=FONT, size=10, italic=True)
    sheet.merge_cells(start_row=1, start_column=7, end_row=1, end_column=11)

    header_row = 2
    for index, (title, width) in enumerate(COLUMNS, start=1):
        cell = sheet.cell(row=header_row, column=index, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )
        cell.border = BORDER
        sheet.column_dimensions[get_column_letter(index)].width = width

    for offset, row in enumerate(rows):
        excel_row = header_row + 1 + offset
        key = progress_key(row)
        printed, notes = progress.get(key, (0, ""))
        values = [
            row["script"],
            row["role"],
            row["source"],
            row["team"],
            row["type"],
            row["reminder"],
            row["qty"],
            printed,
            # Status is a formula so the sheet updates as you tick things off.
            f'=IF(H{excel_row}>=G{excel_row},"Printed",'
            f'IF(H{excel_row}>0,"Partial",""))',
            row["file"],
            notes,
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=excel_row, column=index, value=value)
            cell.font = BODY_FONT
            cell.border = BORDER
            if index in (QTY_NEEDED_COL, QTY_PRINTED_COL, STATUS_COL):
                cell.alignment = Alignment(horizontal="center")
            if index in (QTY_NEEDED_COL, QTY_PRINTED_COL, NOTES_COL):
                cell.fill = INPUT_FILL

    last_row = header_row + len(rows)
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=1)
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(COLUMNS))}{last_row}"

    if rows:
        status_range = f"I{header_row + 1}:I{last_row}"
        sheet.conditional_formatting.add(
            status_range,
            CellIsRule(operator="equal", formula=['"Printed"'], fill=DONE_FILL),
        )
        # Guard against typos in the quantity column.
        validation = DataValidation(
            type="whole", operator="greaterThanOrEqual", formula1=0, allow_blank=True
        )
        validation.error = "Enter a whole number of tokens printed (0 or more)."
        sheet.add_data_validation(validation)
        validation.add(f"H{header_row + 1}:H{last_row}")
    return last_row


def write_summary_sheet(sheet, rows, last_row, scripts):
    sheet.title = "Summary"
    sheet["A1"] = "Printing progress"
    sheet["A1"].font = TITLE_FONT
    sheet.column_dimensions["A"].width = 30
    for column in "BCD":
        sheet.column_dimensions[column].width = 14

    needed = f"Tokens!$G$3:$G${last_row}"
    printed = f"Tokens!$H$3:$H${last_row}"

    def block(title, start_row, pairs, criteria_range=None, pattern="{}"):
        """One titled table of Needed/Printed/Complete rows. Returns the next row.

        Each pair is (label, criterion); a None criterion totals everything.
        ``pattern`` wraps the criterion for SUMIFS, e.g. "*{}*" to match cells
        that merely contain it.
        """
        cell = sheet.cell(row=start_row, column=1, value=title)
        cell.font = Font(name=FONT, size=12, bold=True)
        for index, header in enumerate(["Needed", "Printed", "Complete"], start=2):
            head = sheet.cell(row=start_row + 1, column=index, value=header)
            head.font = HEADER_FONT
            head.fill = HEADER_FILL
            head.alignment = Alignment(horizontal="center")
        for offset, (label, criterion) in enumerate(pairs):
            row = start_row + 2 + offset
            sheet.cell(row=row, column=1, value=label).font = BODY_FONT
            if criterion is None:
                need = f"=SUM({needed})"
                have = f"=SUM({printed})"
            else:
                match = pattern.format(criterion)
                need = f'=SUMIFS({needed},{criteria_range},"{match}")'
                have = f'=SUMIFS({printed},{criteria_range},"{match}")'
            sheet.cell(row=row, column=2, value=need).font = BODY_FONT
            sheet.cell(row=row, column=3, value=have).font = BODY_FONT
            pct = sheet.cell(row=row, column=4, value=f"=IFERROR(C{row}/B{row},0)")
            pct.font = BODY_FONT
            pct.number_format = "0.0%"
            for column in range(2, 5):
                sheet.cell(row=row, column=column).alignment = Alignment(
                    horizontal="center"
                )
        return start_row + 2 + len(pairs)

    next_row = block("Overall", 3, [("All tokens", None)])

    types = f"Tokens!$E$3:$E${last_row}"
    next_row = block(
        "By token type",
        next_row + 1,
        [("Character tokens", "Character"), ("Reminder tokens", "Reminder")],
        types,
    )

    teams = sorted({row["team"] for row in rows})
    next_row = block(
        "By team colour",
        next_row + 1,
        [(team if team.startswith("#") else team.title(), team) for team in teams],
        f"Tokens!$D$3:$D${last_row}",
    )

    sources = sorted({row["source"] for row in rows})
    next_row = block(
        "By source",
        next_row + 1,
        [(source, source) for source in sources],
        f"Tokens!$C$3:$C${last_row}",
    )

    if scripts:
        # A role can appear on several scripts, so its Script cell may list more
        # than one; match on "contains" rather than equality.
        block(
            "By script",
            next_row + 1,
            [(name, name) for name in sorted(scripts)],
            f"Tokens!$A$3:$A${last_row}",
            pattern="*{}*",
        )


def write_howto_sheet(sheet):
    sheet.title = "How to use"
    sheet.column_dimensions["A"].width = 18
    sheet.column_dimensions["B"].width = 88
    sheet["A1"] = "How to use this checklist"
    sheet["A1"].font = TITLE_FONT

    lines = [
        ("Tokens tab", "One row per physical token you need to print."),
        (
            "What to edit",
            "Only the shaded columns: 'Qty needed', 'Qty printed' and 'Notes'. "
            "Everything else is generated or calculated.",
        ),
        (
            "Status",
            "Calculated: 'Printed' once Qty printed reaches Qty needed, "
            "'Partial' in between. Printed rows turn green.",
        ),
        (
            "Qty needed",
            "Usually 1, and higher where a character needs several copies of a "
            "reminder. Edit it for scripts that want many of one token (twelve "
            "Legion, say); your value is kept when the sheet is rebuilt with "
            "--merge, and the plates use it too.",
        ),
        (
            "File (3mf)",
            "The file the generator writes for that token, so you can find it "
            "in the 3mf/ folder.",
        ),
        ("Summary tab", "Live totals overall, by token type, team, source and script."),
        (
            "Regenerating",
            "Re-running the script rebuilds this file. Pass --merge to carry "
            "your Qty printed and Notes across to the new version.",
        ),
    ]
    row = 3
    for label, text in lines:
        label_cell = sheet.cell(row=row, column=1, value=label)
        label_cell.font = Font(name=FONT, size=11, bold=True)
        label_cell.alignment = Alignment(vertical="top")
        body = sheet.cell(row=row, column=2, value=text)
        body.font = BODY_FONT
        body.alignment = Alignment(wrap_text=True, vertical="top")
        row += 1

    row += 1
    sheet.cell(row=row, column=1, value="Example row").font = Font(
        name=FONT, size=12, bold=True
    )
    row += 1
    headers = [title for title, _ in COLUMNS]
    for index, title in enumerate(headers, start=1):
        cell = sheet.cell(row=row, column=index, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
    example = [
        "Trouble Brewing",
        "Poisoner",
        "Official",
        "red",
        "Reminder",
        "Poisoned",
        1,
        1,
        "Printed",
        "3mf/red_Poisoner_Poisoned_reminder.3mf",
        "Reprinted at 0.3mm inlay",
    ]
    for index, value in enumerate(example, start=1):
        cell = sheet.cell(row=row + 1, column=index, value=value)
        cell.font = BODY_FONT
        if index in (QTY_NEEDED_COL, QTY_PRINTED_COL, NOTES_COL):
            cell.fill = INPUT_FILL


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Build an Excel checklist of every printable token."
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            f"Where to write the workbook. Defaults to {DEFAULT_OUTPUT.name}, or "
            "checklist_<script>.xlsx when --script/--script-file is used so the "
            "master sheet is left alone."
        ),
    )
    parser.add_argument(
        "--script",
        metavar="NAME",
        help=f"Only include roles from this script, as defined in {SCRIPTS_JSON.name}.",
    )
    parser.add_argument(
        "--script-file",
        metavar="FILE",
        help=(
            "Build the sheet from an official script-creator JSON file, without "
            "adding it to scripts.json first."
        ),
    )
    parser.add_argument(
        "--no-custom",
        action="store_true",
        help=f"Ignore {CUSTOM_ROLES_JSON.name}.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help=(
            "Allow rebuilding over a checklist that has recorded progress, "
            "discarding it. Without this, such a rebuild is refused."
        ),
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Carry Qty printed and Notes over from the existing workbook.",
    )
    parser.add_argument(
        "--merge-from",
        metavar="FILE",
        help=(
            "Carry progress over from this workbook instead of --output. Use it "
            "to build a script-specific sheet seeded from your master one."
        ),
    )
    args = parser.parse_args(argv)

    custom_files = (
        []
        if args.no_custom
        else ([CUSTOM_ROLES_JSON] if CUSTOM_ROLES_JSON.exists() else [])
    )
    if not ROLES_JSON.exists() and not custom_files:
        parser.error(f"{ROLES_JSON.name} not found. Run: python get_all_roles.py")

    try:
        roles = load_role_definitions(ROLES_JSON, custom_files)
        scripts = load_scripts(SCRIPTS_JSON)
        scoped_name = None
        if args.script_file:
            name, author, role_ids, _ = parse_script_file(args.script_file)
            # Show the script's own name in the sheet, and scope to its roles.
            scripts = {name: role_ids}
            roles = filter_roles_by_script(roles, name, scripts)
            scoped_name = name
            by_line = f" by {author}" if author else ""
            print(f"Script {name!r}{by_line} from {args.script_file}")
        elif args.script:
            roles = filter_roles_by_script(roles, args.script, scripts)
            scoped_name = args.script
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error).strip('"'))

    # Choose the output file. An explicit --output always wins. Otherwise a
    # scoped run writes checklist_<script>.xlsx so it can't overwrite the master
    # print_checklist.xlsx -- matching the GUI, and avoiding a --script run
    # silently clobbering your full sheet.
    if not args.output and scoped_name:
        args.output = str(scoped_checklist_path(scoped_name))
    elif not args.output:
        args.output = str(DEFAULT_OUTPUT)

    rows = build_inventory(roles, scripts)

    merge_source = args.merge_from or (args.output if args.merge else None)
    progress = read_existing_progress(merge_source) if merge_source else {}
    # Quantities you edited are configuration, not progress, so carry them over
    # whenever we are reading an existing sheet at all.
    quantities = read_quantity_overrides(merge_source) if merge_source else {}
    apply_quantity_overrides(rows, quantities)

    # Rebuilding writes a fresh sheet, so anything already ticked off in the
    # output file is discarded. Losing that silently is the sort of thing you
    # only notice much later, so make it a deliberate choice. Edited quantities
    # count too: they are hand-entered and just as annoying to lose.
    if not merge_source and not args.overwrite:
        existing = read_existing_progress(args.output)
        generated = {progress_key(row): row["qty"] for row in rows}
        edited = {
            key: qty
            for key, qty in read_quantity_overrides(args.output).items()
            if qty != generated.get(key, qty)
        }
        if existing or edited:
            parts = []
            if existing:
                parts.append(f"progress for {len(existing)} row(s)")
            if edited:
                parts.append(f"{len(edited)} edited quantity/quantities")
            parser.error(
                f"{args.output} already records {' and '.join(parts)}, and "
                "rebuilding would discard that.\n"
                "  Use --merge to keep it, or --overwrite to start fresh.\n"
                "  To build a separate sheet instead, add --output OTHER.xlsx "
                "(and --merge-from to seed it)."
            )

    write_checklist(rows, args.output, progress, scripts)

    characters = sum(1 for row in rows if row["type"] == "Character")
    reminders = sum(row["qty"] for row in rows if row["type"] == "Reminder")
    print(
        f"Wrote {args.output}: {len(rows)} rows "
        f"({characters} character tokens, {reminders} reminder tokens)."
    )

    if progress:
        keys = {progress_key(row) for row in rows}
        carried = sum(1 for key in progress if key in keys)
        print(f"Carried over progress for {carried} row(s) from {merge_source}.")
        # Narrowing to a separate file is the normal --script workflow, and the
        # left-out roles are simply the ones you filtered out, so say nothing.
        # It only matters when the narrowed sheet overwrites the file the
        # progress came from, because then those ticks are genuinely gone.
        if Path(args.output) == Path(merge_source):
            dropped = sorted({key[0] for key in progress if key not in keys})
            if dropped:
                print(
                    f"  ! You have just narrowed {args.output}, which had "
                    f"progress for {len(dropped)} role(s) that are not in this "
                    f"sheet ({', '.join(dropped[:5])}"
                    f"{'...' if len(dropped) > 5 else ''}). That progress is no "
                    "longer in this file - restore it from a backup, or rebuild "
                    "the full sheet without --script."
                )


if __name__ == "__main__":
    main()
