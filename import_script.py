"""Import official script-creator JSON files into this project.

The script tool at script.bloodontheclocktower.com (and the script-hosting
sites) exports a .json listing a script's characters. This adds those scripts to
scripts.json so you can use them by name:

    uv run python import_script.py Catfishing.json
    uv run python solid_maker.py --script "Catfishing"
    uv run python make_print_checklist.py --script "Catfishing"

Several files can be imported at once, and re-importing updates a script in
place. Homebrew characters defined inside a script file are written to
custom_roles.json so their tokens can be generated too.
"""

import argparse
import json
from pathlib import Path

from solid_maker import (
    CUSTOM_ROLES_JSON,
    ROLES_JSON,
    SCRIPTS_JSON,
    homebrew_to_role_entry,
    load_json_file,
    load_role_definitions,
    normalise_role_name,
    parse_script_file,
    team_colour_map,
)


def _load_existing(path):
    """Read a JSON object we are about to update, or start a fresh one."""
    if not Path(path).exists():
        return {}
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def import_scripts(
    files,
    scripts_json=SCRIPTS_JSON,
    custom_json=CUSTOM_ROLES_JSON,
    roles_json=ROLES_JSON,
    write_homebrew=True,
):
    """Merge each script file into scripts.json. Returns a per-file report."""
    scripts = _load_existing(scripts_json)
    custom = _load_existing(custom_json)
    known = load_role_definitions(roles_json, [])
    colours = team_colour_map(known)
    known_keys = {normalise_role_name(name) for name in known}
    known_keys |= {normalise_role_name(name) for name in custom if name != "_comment"}

    reports = []
    for path in files:
        name, author, role_ids, homebrew = parse_script_file(path)

        added_homebrew = []
        if write_homebrew:
            for entry in homebrew:
                label = entry.get("name") or entry.get("id")
                if not label:
                    continue
                custom[label] = homebrew_to_role_entry(entry, colours)
                known_keys.add(normalise_role_name(label))
                added_homebrew.append(label)

        # Store the ids as written; role-name matching is loose, so
        # "fortuneteller" finds "Fortune Teller" without a lookup table.
        replaced = name in scripts
        scripts[name] = role_ids

        unknown = sorted(
            {rid for rid in role_ids if normalise_role_name(rid) not in known_keys}
        )
        reports.append(
            {
                "file": str(path),
                "name": name,
                "author": author,
                "roles": len(role_ids),
                "replaced": replaced,
                "homebrew": added_homebrew,
                "unknown": unknown,
            }
        )

    _write_json(scripts_json, scripts)
    if write_homebrew and any(report["homebrew"] for report in reports):
        _write_json(custom_json, custom)
    return reports


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import official script-creator JSON files into scripts.json."
    )
    parser.add_argument("files", nargs="+", help="Script .json file(s) to import.")
    parser.add_argument(
        "--scripts-json",
        default=str(SCRIPTS_JSON),
        help=f"Where to write the scripts (default: {SCRIPTS_JSON.name}).",
    )
    parser.add_argument(
        "--no-homebrew",
        action="store_true",
        help=(
            f"Do not copy homebrew characters defined inside the script file "
            f"into {CUSTOM_ROLES_JSON.name}."
        ),
    )
    args = parser.parse_args(argv)

    try:
        reports = import_scripts(
            args.files,
            scripts_json=Path(args.scripts_json),
            write_homebrew=not args.no_homebrew,
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))

    for report in reports:
        verb = "Updated" if report["replaced"] else "Added"
        by_line = f" by {report['author']}" if report["author"] else ""
        print(f"{verb} script {report['name']!r}{by_line}: {report['roles']} role(s)")
        if report["homebrew"]:
            print(
                f"  Imported {len(report['homebrew'])} homebrew character(s) into "
                f"{CUSTOM_ROLES_JSON.name}: {', '.join(report['homebrew'][:8])}"
                f"{'...' if len(report['homebrew']) > 8 else ''}"
            )
        if report["unknown"]:
            # Named, because the fix is either scraping roles.json again or
            # adding the character by hand.
            print(
                f"  ! {len(report['unknown'])} character(s) have no definition "
                f"yet and will be skipped when generating: "
                f"{', '.join(report['unknown'][:8])}"
                f"{'...' if len(report['unknown']) > 8 else ''}"
            )

    print(f"\nWrote {args.scripts_json}. Use it with:")
    print(f'  uv run python solid_maker.py --script "{reports[0]["name"]}"')
    print(f'  uv run python make_print_checklist.py --script "{reports[0]["name"]}"')


if __name__ == "__main__":
    main()
