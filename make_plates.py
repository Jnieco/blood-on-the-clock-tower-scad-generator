"""Arrange finished tokens onto build plates, grouped by filament colour.

Instead of dragging tokens into the slicer one at a time, this packs them into a
few ready-to-slice plates:

    uv run python make_plates.py
    uv run python make_plates.py --script "Trouble Brewing"
    uv run python make_plates.py --bed 256x256 --gap 4

Each plate holds only ONE token colour plus the dark disc, so a plate needs just
two filaments and one AMS swap between plates -- and since the colour is only in
the top 0.3 mm, that is most of the filament saving. Plates are written to
plates/ as plate_01_red.3mf, plate_02_blue.3mf, and so on.

The tokens must already be generated (their base STLs and overlay STLs exist);
run solid_maker.py first, or use --generate to do both in one step.
"""

import argparse
import math
from collections import defaultdict
from pathlib import Path

import make_print_checklist as mpc
import solid_maker as sm


def _token_key(role, reminder):
    """The checklist identity for a token: (role, type, reminder)."""
    if reminder is None:
        return (role, "Character", "")
    return (role, "Reminder", reminder)


def collect_tokens(roles, skip_keys=None, reprint_roles=None):
    """Every token to place: (colour, colour_hex, base_stl, overlay_stl, label).

    Duplicate reminders collapse to one token here; a plate shows one of each,
    and the checklist tracks how many copies to print.

    ``skip_keys`` is a set of (role, type, reminder) checklist keys to leave off
    the plate (already printed). ``reprint_roles`` is a set of role names whose
    tokens are always included even if in ``skip_keys`` -- forced reprints.
    """
    skip_keys = skip_keys or set()
    reprint = {r.lower() for r in (reprint_roles or set())}
    tokens = []
    coin_base = sm.STL_DIR / f"{sm.COIN_BASE_NAME}.stl"
    reminder_base = sm.STL_DIR / f"{sm.REMINDER_BASE_NAME}.stl"

    def wanted(role, reminder):
        if role.lower() in reprint:
            return True  # forced back on, regardless of printed state
        return _token_key(role, reminder) not in skip_keys

    for role, data in sorted(roles.items(), key=lambda kv: kv[0].lower()):
        colour = data.get("color", "unknown")
        colour_hex = sm.overlay_colour_hex(colour)
        role_safe = sm.safe_name(role)
        colour_safe = sm.safe_name(colour)

        overlay = sm.STL_DIR / f"{colour_safe}_{role_safe}_coin_overlay.stl"
        if overlay.exists() and wanted(role, None):
            tokens.append((colour, colour_hex, coin_base, overlay, f"{role}"))

        for label in dict.fromkeys(sm.role_reminders(data)):
            reminder_safe = sm.safe_name(label)
            overlay = (
                sm.STL_DIR / f"{colour_safe}_{role_safe}_{reminder_safe}_reminder.stl"
            )
            if overlay.exists() and wanted(role, label):
                tokens.append(
                    (colour, colour_hex, reminder_base, overlay, f"{role}: {label}")
                )
    return tokens


def _grid_positions(count, cell, bed, margin):
    """Centre positions for `count` cells of size `cell` mm within the bed.

    Returns (positions, per_plate, cols, rows): a flat list of (plate_index,
    x, y), how many cells fit on one plate, and the column/row counts. Rows fill
    left-to-right, top-to-bottom, and each plate's used cells are centred on the
    bed so a partial plate doesn't cluster in one corner.
    """
    usable_w = bed[0] - 2 * margin
    usable_d = bed[1] - 2 * margin
    cols = max(int(usable_w // cell), 1)
    rows = max(int(usable_d // cell), 1)
    per_plate = cols * rows

    # How many cells (and therefore rows) each plate actually uses, so a partial
    # final plate is centred rather than hugging the top rows.
    def layout_for(on_this_plate):
        used_cols = min(cols, on_this_plate)
        used_rows = math.ceil(on_this_plate / cols)
        return used_cols, used_rows

    positions = []
    for index in range(count):
        plate = index // per_plate
        slot = index % per_plate
        col = slot % cols
        row = slot // cols

        on_this_plate = min(per_plate, count - plate * per_plate)
        used_cols, used_rows = layout_for(on_this_plate)
        # Width of the current row: the last row may be shorter than used_cols.
        cells_in_row = min(used_cols, on_this_plate - row * cols)

        row_w = cells_in_row * cell
        block_d = used_rows * cell
        x = -row_w / 2 + cell / 2 + col * cell
        y = block_d / 2 - cell / 2 - row * cell
        positions.append((plate, x, y))
    return positions, per_plate, cols, rows


def _plate_model_xml(placements, colour_hex):
    """A 3dmodel.model holding many tokens, each placed by a transform.

    Every token contributes a disc object and an inlay object (shared colour
    group), and one <item> per body carries the translation onto the plate.
    """
    colorgroup = (
        f'<m:colorgroup id="{sm._COLORGROUP_ID}">'
        f'<m:color color="{sm.BASE_COLOR_HEX}"/>'
        f'<m:color color="{colour_hex}"/>'
        "</m:colorgroup>"
    )
    objects = []
    items = []
    next_id = 2  # 1 is the colour group
    for (disc_v, disc_f), (over_v, over_f), (x, y) in placements:
        disc_id, inlay_id = next_id, next_id + 1
        next_id += 2
        objects.append(
            f'<object id="{disc_id}" type="model" name="disc">'
            f"{sm._face_coloured_mesh_xml([(disc_v, disc_f, 0)])}</object>"
        )
        objects.append(
            f'<object id="{inlay_id}" type="model" name="inlay">'
            f"{sm._face_coloured_mesh_xml([(over_v, over_f, 1)])}</object>"
        )
        # 3MF transforms are a row-major 4x3 affine matrix written as 12 numbers.
        transform = f"1 0 0 0 1 0 0 0 1 {x:.4f} {y:.4f} 0"
        items.append(f'<item objectid="{disc_id}" transform="{transform}"/>')
        items.append(f'<item objectid="{inlay_id}" transform="{transform}"/>')

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{sm._3MF_CORE_NS}" '
        f'xmlns:m="{sm._3MF_MAT_NS}">'
        f"<resources>{colorgroup}{''.join(objects)}</resources>"
        f"<build>{''.join(items)}</build>"
        "</model>"
    )


def build_plates(
    roles,
    bed=(256, 256),
    gap=3.0,
    margin=5.0,
    art_on_bottom=True,
    out_dir=None,
    skip_keys=None,
    reprint_roles=None,
):
    """Write one .3mf per plate, grouped by colour. Returns a per-plate report."""
    out_dir = Path(out_dir or (sm.PROJECT_ROOT / "plates"))
    out_dir.mkdir(parents=True, exist_ok=True)

    tokens = collect_tokens(roles, skip_keys=skip_keys, reprint_roles=reprint_roles)
    if not tokens:
        if skip_keys:
            # The common, friendly case: everything is already printed.
            raise ValueError(
                "Nothing to plate - every token in this selection is already "
                "marked printed. Use --all to plate them anyway, or --reprint "
                "ROLE to force specific ones back on."
            )
        raise ValueError(
            "No token STLs found. Generate the tokens first "
            "(solid_maker.py), or pass --generate."
        )

    by_colour = defaultdict(list)
    for colour, colour_hex, base_stl, overlay_stl, label in tokens:
        by_colour[(colour, colour_hex)].append((base_stl, overlay_stl, label))

    reports = []
    plate_number = 0
    total_colours = len(by_colour)
    for colour_index, ((colour, colour_hex), group) in enumerate(
        sorted(by_colour.items()), start=1
    ):
        print(
            f"[progress] {colour_index}/{total_colours} {colour} plate(s)", flush=True
        )
        # Load geometry once per token and find the largest so cells are uniform.
        loaded = []
        max_radius = 0.0
        for base_stl, overlay_stl, label in group:
            disc, inlay = sm._token_bodies(base_stl, overlay_stl, art_on_bottom)
            radius = max(sm._token_radius(disc[0]), sm._token_radius(inlay[0]))
            max_radius = max(max_radius, radius)
            loaded.append((disc, inlay, label))

        cell = 2 * max_radius + gap
        positions, _per_plate, cols, rows = _grid_positions(
            len(loaded), cell, bed, margin
        )

        plates = defaultdict(list)
        plate_labels = defaultdict(list)
        for (disc, inlay, label), (plate, x, y) in zip(loaded, positions, strict=False):
            plates[plate].append((disc, inlay, (x, y)))
            plate_labels[plate].append(label)

        colour_safe = sm.safe_name(colour)
        for local_plate in sorted(plates):
            plate_number += 1
            model = _plate_model_xml(plates[local_plate], colour_hex)
            out_path = out_dir / f"plate_{plate_number:02d}_{colour_safe}.3mf"
            sm._write_3mf_package(out_path, model)
            reports.append(
                {
                    "file": out_path,
                    "colour": colour,
                    "tokens": len(plates[local_plate]),
                    "grid": f"{cols}x{rows}",
                    "labels": plate_labels[local_plate],
                }
            )
            print(
                f"[ok] {out_path.name}: {len(plates[local_plate])} {colour} "
                f"token(s), {cols}x{rows} grid"
            )
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Arrange generated tokens onto build plates, grouped by colour."
    )
    parser.add_argument(
        "--bed",
        type=sm.parse_bed_size,
        default=(256, 256),
        help="Bed size in mm as WIDTHxDEPTH (default 256x256, an X1/P1).",
    )
    parser.add_argument(
        "--gap", type=float, default=3.0, help="Gap between tokens in mm (default 3)."
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=5.0,
        help="Clear margin around the plate edge in mm (default 5).",
    )
    parser.add_argument("--script", metavar="NAME", help="Only this script's tokens.")
    parser.add_argument(
        "--script-file", metavar="FILE", help="Only this script file's tokens."
    )
    parser.add_argument("--no-custom", action="store_true", help="Ignore custom roles.")
    parser.add_argument("--art-up", action="store_true", help="Art faces up.")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate any missing token STLs first (runs solid_maker).",
    )
    parser.add_argument(
        "--out-dir", default=None, help="Where to write the plates (default: plates/)."
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Include every token, even ones marked printed in the checklist. By "
            "default, already-printed tokens are left off the plate."
        ),
    )
    parser.add_argument(
        "--reprint",
        metavar="ROLE",
        action="append",
        default=None,
        help=(
            "Force this role's tokens onto the plate even if printed. May be "
            "repeated. Implies those roles are wanted regardless of --all."
        ),
    )
    parser.add_argument(
        "--choose-tokens",
        action="store_true",
        help='Also lay out the four "Choose a ..." draft tokens.',
    )
    parser.add_argument(
        "--choose-only",
        action="store_true",
        help='Lay out only the "Choose a ..." draft tokens.',
    )
    parser.add_argument(
        "--checklist",
        metavar="FILE",
        default=None,
        help=(
            "Checklist workbook to read printed state from. Defaults to the one "
            "for this selection (checklist_<script>.xlsx or print_checklist.xlsx)."
        ),
    )
    args = parser.parse_args(argv)

    try:
        if args.choose_only:
            roles, scripts = {}, {}
        else:
            roles, scripts = sm.load_selected_roles(
                sm.default_custom_files(args.no_custom),
                script=args.script,
                script_file=args.script_file,
            )
        if args.choose_tokens or args.choose_only:
            # The draft tokens are built-in and text-only; they are laid out by
            # the same colour-grouping as any other character token.
            roles = {**roles, **sm.choose_token_definitions()}
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error).strip('"'))

    if args.generate:
        generate_args = []
        if args.script:
            generate_args += ["--script", args.script]
        if args.script_file:
            generate_args += ["--script-file", args.script_file]
        if args.no_custom:
            generate_args.append("--no-custom")
        if args.art_up:
            generate_args.append("--art-up")
        if args.choose_tokens or args.choose_only:
            generate_args.append(
                "--choose-only" if args.choose_only else "--choose-tokens"
            )
        print("Generating any missing tokens first...")
        sm.main(generate_args)

    # Work out which tokens the checklist says are already fully printed, so
    # they can be left off the plate (unless --all, or a --reprint override).
    skip_keys = set()
    if not args.all:
        checklist = (
            Path(args.checklist)
            if args.checklist
            else mpc.checklist_path_for(
                script=args.script, script_file=args.script_file
            )
        )
        if checklist.exists():
            rows = mpc.build_inventory(roles, scripts)
            skip_keys = mpc.read_printed_set(checklist, rows)
            if skip_keys:
                print(
                    f"Skipping {len(skip_keys)} already-printed token(s) from "
                    f"{checklist.name}. Use --all to include them."
                )
        elif not args.reprint:
            print(
                f"No checklist found at {checklist.name}; plating every token. "
                "Mark tokens printed there to have them skipped next time."
            )

    try:
        reports = build_plates(
            roles,
            bed=args.bed,
            gap=args.gap,
            margin=args.margin,
            art_on_bottom=not args.art_up,
            out_dir=args.out_dir,
            skip_keys=skip_keys,
            reprint_roles=set(args.reprint or []),
        )
    except ValueError as error:
        parser.error(str(error))

    total = sum(report["tokens"] for report in reports)
    print(
        f"\nWrote {len(reports)} plate(s) holding {total} token(s) to "
        f"{args.out_dir or 'plates/'}. Each plate uses the dark disc plus one "
        "colour, so load those two filaments and slice."
    )


if __name__ == "__main__":
    main()
