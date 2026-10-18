"""Check every role's art before a big print run, in one batch.

Rendering already warns about weak art, but only as you hit each token mid-run.
This looks at all of it up front and prints one report, so you can fix the
sources together:

    uv run python art_preflight.py
    uv run python art_preflight.py --script "Trouble Brewing"

It downloads or copies each role's art and flattens it to greyscale (the same
first step the generator does), then analyses it. No SCAD or STL work, so it is
much faster than a full build. Text-only roles are skipped; they have no art.

Exit status is 1 if any art has a problem, so it can gate a scripted run.
"""

import argparse
import sys

import solid_maker as sm


def preflight_role(role, data):
    """Prepare and analyse one role's art. Returns a result dict, or None.

    None means the role has no art (a text-only token), so there is nothing to
    check.
    """
    image_spec = data.get("image")
    if not sm.has_art(image_spec):
        return None

    try:
        grey_file = sm.prepare_role_art(role, image_spec)
        problems, stats = sm.analyse_art(grey_file)
    except Exception as error:  # noqa: BLE001 - reported per role, never fatal
        return {"role": role, "error": str(error)}

    return {
        "role": role,
        "warnings": problems,
        "ink_fraction": stats["ink_fraction"],
        "thin_loss": stats["thin_loss"],
    }


def run_preflight(roles, quiet=False):
    """Analyse every role's art. Returns (results, problem_count)."""
    for directory in (sm.PNG_DIR, sm.GREY_PNG_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    results = []
    for role, data in sorted(roles.items(), key=lambda kv: kv[0].lower()):
        if not quiet:
            print(f"[progress] {len(results) + 1}/{len(roles)} {role}", flush=True)
        result = preflight_role(role, data)
        if result is not None:
            results.append(result)

    problems = [r for r in results if r.get("error") or r.get("warnings")]
    return results, problems


def format_report(results, problems):
    """A short text report: the problems first, then a one-line summary."""
    lines = []
    checked = len(results)
    text_only = "  (text-only roles were skipped)"

    if not problems:
        lines.append(f"All {checked} role(s) with art look fine.{text_only}")
        return "\n".join(lines)

    lines.append(f"{len(problems)} of {checked} role(s) with art need a look:")
    lines.append("")
    for result in sorted(problems, key=lambda r: r["role"].lower()):
        if result.get("error"):
            lines.append(f"  {result['role']}: {result['error']}")
            continue
        for warning in result["warnings"]:
            lines.append(f"  {result['role']}: {warning}")
    lines.append("")
    lines.append(
        f"{checked - len(problems)} other role(s) with art look fine.{text_only}"
    )
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check every role's art for print problems, in one batch."
    )
    parser.add_argument("--script", metavar="NAME", help="Only this script's roles.")
    parser.add_argument(
        "--script-file", metavar="FILE", help="Only this script file's roles."
    )
    parser.add_argument("--no-custom", action="store_true", help="Ignore custom roles.")
    args = parser.parse_args(argv)

    try:
        roles, _scripts = sm.load_selected_roles(
            sm.default_custom_files(args.no_custom),
            script=args.script,
            script_file=args.script_file,
        )
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error).strip('"'))

    results, problems = run_preflight(roles)
    print("\n" + format_report(results, problems))
    # Non-zero exit if anything needs attention, so a script can gate on it.
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
