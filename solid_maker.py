"""Generate OpenSCAD/STL files for Blood on the Clocktower tokens.

Produces two kinds of tokens:

* Character tokens (45mm) - role art plus the role name curved around the rim.
* Reminder tokens (25mm) - role art plus straight, wrapped reminder text.

Each token is emitted as a *base* disc and a separate *overlay* so the two can
be printed in different filament colours (or with a filament change).

This module is cross-platform. It needs only Python packages plus OpenSCAD;
ImageMagick and the potrace binary are no longer required, because tracing is
done in pure Python via the `potracer` package.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import warnings
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np
import potrace
import requests
from PIL import Image, ImageFilter, ImageFont

# solidpython 1.1.3 does `import pkg_resources` at import time, which setuptools
# now warns about on every single run. Nothing here can act on it (the fix is
# upstream), so keep it out of the output rather than training people to ignore
# warnings. The setuptools<81 pin in pyproject.toml is what actually keeps
# pkg_resources available.
warnings.filterwarnings(
    "ignore", message="pkg_resources is deprecated", category=UserWarning
)

from solid import (  # noqa: E402  (must follow the warning filter above)
    circle,
    cylinder,
    intersection,
    linear_extrude,
    offset,
    polygon,
    rotate,
    scad_render_to_file,
    text,
    translate,
    union,
)

# --------------------------------------------------------------------------
# Dimensional constants (mm)
# --------------------------------------------------------------------------
COIN_DIAMETER = 45.0
COIN_HEIGHT = 2.0
ROLE_EXTRUDE_DEPTH = 0.3
TEXT_SIZE = 5.2
# Max diameter the role art is scaled to on a character token, and how far up it
# is nudged. The name curves round the bottom, so shifting the art up into the
# empty top lets it be larger. Long names reach round the sides, so the art is
# fitted per token (see role_overlay_model) to keep this clearance from the name.
ROLE_ICON_DIAMETER = 30.5
# The name only occupies the bottom of the disc. This nudges the art a little
# way UP THE FACE (in-plane, not in Z) so it sits just clear of the text rather
# than touching it. It does not raise the art off the disc -- the art is a flush
# inlay, so the token still prints perfectly flat, art-side-down, with no
# supports, whatever this value is.
ROLE_ICON_SHIFT_Y = 1.5
ROLE_ICON_CLEARANCE = 0.6
# How far the curved name's baseline sits in from the rim. Pulling it out a little
# leaves more centre room for a disc-centred icon.
TEXT_BASELINE_INSET = 1.5

# Reminder tokens are smaller than character tokens, matching the real game.
REMINDER_DIAMETER = 25.0
REMINDER_HEIGHT = 2.0
REMINDER_TEXT_SIZE = 3.7
REMINDER_TEXT_MIN_SIZE = 2.0
REMINDER_MAX_LINES = 2
# Text sits low on the disc so the art above it can be as large as possible.
REMINDER_TEXT_BASELINE_FRACTION = 0.78
# Largest the art is ever allowed to get; it shrinks further if the text needs
# two lines.
REMINDER_ICON_MAX_FRACTION = 0.85
REMINDER_ICON_TEXT_GAP = 0.6
# The reminder art is small (~15 mm), so fine traced strokes can fall below what
# the nozzle can lay down in a second colour. Fatten the whole shape slightly so
# thin features survive at that scale. 0 disables it.
REMINDER_ICON_DILATE = 0.25

FONT = "Dumbledor 1 Fixed"

# --------------------------------------------------------------------------
# Two-colour 3MF export
# --------------------------------------------------------------------------
# Each finished token is also written as a single .3mf holding two bodies -- the
# disc and the raised relief -- each tagged with a display colour. BambuStudio
# reads these colours on import and maps them to filaments, so the whole token
# drags in as one ready-to-print two-colour object.
BASE_COLOR_HEX = "#2E2E2EFF"  # the disc / "felt" underside
OVERLAY_COLOR_BY_NAME = {
    "blue": "#3297F4FF",
    "red": "#8C0E12FF",
    "purple": "#800080FF",
    "yellow": "#D4AF37FF",
    "green": "#3F9651FF",
}
DEFAULT_OVERLAY_COLOR_HEX = "#9AA0A6FF"  # used when a role's team colour is unknown


# Anchor every path to the app rather than the current working directory.
# VS Code and PowerShell often launch with a different CWD, which silently
# broke the original relative paths.
def _app_dir() -> Path:
    """Folder holding your data: roles.json, settings.json, the output folders.

    When packaged as a .exe this is the folder the .exe sits in, so everything
    stays next to it rather than in a temporary unpack directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_dir() -> Path:
    """Folder holding bundled read-only resources such as the font."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


PROJECT_ROOT = _app_dir()
ASSETS_DIR = _resource_dir() / "assets"
FONT_FILE = ASSETS_DIR / "Dumbledor1_fixed.ttf"

PNG_DIR = PROJECT_ROOT / "pngs"
GREY_PNG_DIR = PROJECT_ROOT / "grey_pngs"
SVG_DIR = PROJECT_ROOT / "svgs"
SCAD_DIR = PROJECT_ROOT / "scads"
STL_DIR = PROJECT_ROOT / "stls"
THREEMF_DIR = PROJECT_ROOT / "3mf"
ROLES_JSON = PROJECT_ROOT / "roles.json"
# Homebrew/custom tokens live here, in the same schema as roles.json. Entries
# are merged last, so they can add new characters or override official ones.
CUSTOM_ROLES_JSON = PROJECT_ROOT / "custom_roles.json"
# Extra reminder labels to add on top of whatever a role already has. Keyed by
# role name (loosely matched), value is a list of strings. Handy when the wiki
# is missing a reminder a role actually needs (the Harpy), or for adding your
# own to a homebrew role without redefining the whole thing.
EXTRA_REMINDERS_JSON = PROJECT_ROOT / "extra_reminders.json"
# Optional: named scripts/editions -> the roles they contain. Lets you print or
# track one script's tokens instead of every role.
SCRIPTS_JSON = PROJECT_ROOT / "scripts.json"

# Warn (don't fail) when custom art looks unlikely to trace or print well.
# The reminder token is the small case, so judge art at that size.
ART_MIN_INK_FRACTION = 0.02  # nearly-blank art: probably the wrong file
ART_MAX_INK_FRACTION = 0.92  # nearly-solid art: probably not transparent-backed
ART_MIN_FEATURE_MM = 0.4  # strokes thinner than this tend to vanish when printed
ART_THIN_FEATURE_LOSS = 0.6  # fraction of ink lost to erosion before we warn


def load_json_file(path):
    """json.load with an error message a human can act on.

    A malformed hand-edited file otherwise surfaces as a bare JSONDecodeError
    traceback, which says little about which file or what to fix.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")  # tolerate a BOM from Notepad
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        lines = text.splitlines()
        line = lines[error.lineno - 1] if 0 < error.lineno <= len(lines) else ""
        pointer = " " * max(error.colno - 1, 0) + "^"
        hint = ""
        if any(ch in text for ch in "\u201c\u201d\u2018\u2019"):
            # Almost certainly the cause, so don't also offer a generic guess.
            hint = (
                "\n  Hint: this file contains curly quotes. JSON needs plain "
                'straight quotes ("), so re-type any pasted text.'
            )
        elif "Expecting ',' delimiter" in error.msg:
            hint = (
                "\n  Hint: usually a missing comma between entries, a trailing "
                "comma before ] or }, or a stray quote inside a name."
            )
        elif "Expecting property name" in error.msg:
            hint = "\n  Hint: usually a trailing comma after the last entry."
        raise ValueError(
            f"{path} is not valid JSON: {error.msg} "
            f"(line {error.lineno}, column {error.colno})\n"
            f"  {line}\n  {pointer}{hint}"
        ) from error


# --------------------------------------------------------------------------
# settings.json: the knobs worth tuning, without editing this file
# --------------------------------------------------------------------------
# Keeping these in a data file means your tuning survives dropping in a new
# version of this script. Each entry is (type, minimum, maximum); None means
# unbounded. Only these names can be set - a typo is reported rather than
# silently ignored.
SETTINGS_JSON = PROJECT_ROOT / "settings.json"

TUNABLE_SETTINGS = {
    # Character token
    "COIN_DIAMETER": (float, 10.0, 120.0),
    "COIN_HEIGHT": (float, 0.5, 20.0),
    "TEXT_SIZE": (float, 1.0, 15.0),
    "TEXT_BASELINE_INSET": (float, 0.0, 10.0),
    "ROLE_ICON_DIAMETER": (float, 5.0, 120.0),
    "ROLE_ICON_SHIFT_Y": (float, -15.0, 15.0),
    "ROLE_ICON_CLEARANCE": (float, 0.0, 10.0),
    # How deep the colour inlay is; more layers means better colour coverage.
    "ROLE_EXTRUDE_DEPTH": (float, 0.05, 2.0),
    # Reminder token
    "REMINDER_DIAMETER": (float, 5.0, 120.0),
    "REMINDER_HEIGHT": (float, 0.5, 20.0),
    "REMINDER_TEXT_SIZE": (float, 1.0, 12.0),
    "REMINDER_TEXT_MIN_SIZE": (float, 0.5, 12.0),
    "REMINDER_MAX_LINES": (int, 1, 4),
    "REMINDER_TEXT_BASELINE_FRACTION": (float, 0.0, 1.0),
    "REMINDER_ICON_MAX_FRACTION": (float, 0.1, 1.0),
    "REMINDER_ICON_TEXT_GAP": (float, 0.0, 10.0),
    "REMINDER_ICON_DILATE": (float, 0.0, 2.0),
    # Appearance
    "FONT": (str, None, None),
    "BASE_COLOR_HEX": (str, None, None),
    "DEFAULT_OVERLAY_COLOR_HEX": (str, None, None),
    # Art warnings
    "ART_MIN_FEATURE_MM": (float, 0.0, 5.0),
}
# Team colours are a mapping, so they are merged rather than range-checked.
SETTINGS_COLOUR_KEY = "OVERLAY_COLOR_BY_NAME"


def _check_setting(name, value):
    """Return (converted_value, problem). problem is None when the value is ok."""
    kind, low, high = TUNABLE_SETTINGS[name]
    if kind is str:
        if not isinstance(value, str) or not value.strip():
            return None, f"{name}: expected a non-empty string, got {value!r}"
        return value, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{name}: expected a number, got {value!r}"
    value = kind(value)
    if (low is not None and value < low) or (high is not None and value > high):
        return None, f"{name}: {value} is outside the sensible range {low} to {high}"
    return value, None


def apply_settings(data, namespace=None):
    """Apply a settings mapping over the module's defaults.

    Returns the list of problems found; valid values are applied regardless, so
    one bad entry doesn't discard the rest.
    """
    namespace = globals() if namespace is None else namespace
    problems = []
    for name, value in (data or {}).items():
        if name.startswith("_"):
            continue  # allow "_comment" documentation keys
        if name == SETTINGS_COLOUR_KEY:
            if not isinstance(value, dict):
                problems.append(f"{name}: expected an object of team -> colour")
                continue
            merged = dict(namespace["OVERLAY_COLOR_BY_NAME"])
            for team, colour in value.items():
                if not isinstance(colour, str) or not colour.startswith("#"):
                    problems.append(f"{name}.{team}: expected a '#RRGGBB' colour")
                    continue
                merged[str(team).lower()] = (
                    colour if len(colour) == 9 else f"{colour}FF"
                ).upper()
            namespace["OVERLAY_COLOR_BY_NAME"] = merged
            continue
        if name not in TUNABLE_SETTINGS:
            near = difflib.get_close_matches(name, list(TUNABLE_SETTINGS), 1, 0.6)
            hint = f" Did you mean {near[0]!r}?" if near else ""
            problems.append(f"{name}: not a setting.{hint}")
            continue
        converted, problem = _check_setting(name, value)
        if problem:
            problems.append(problem)
        else:
            namespace[name] = converted
    return problems


def load_settings(path=None):
    """Read settings.json if present. Returns {} when there is none."""
    path = Path(path or SETTINGS_JSON)
    if not path.exists():
        return {}
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object of setting -> value")
    return data


def current_settings():
    """The present value of every tunable, for writing a starter file."""
    values = {name: globals()[name] for name in TUNABLE_SETTINGS}
    values[SETTINGS_COLOUR_KEY] = dict(OVERLAY_COLOR_BY_NAME)
    return values


def validate_setting(name, value):
    """Check one (name, value) without applying it. Returns a problem or None."""
    if name == SETTINGS_COLOUR_KEY:
        return None  # colours are validated as a mapping in apply_settings
    if name not in TUNABLE_SETTINGS:
        return f"{name}: not a setting"
    _converted, problem = _check_setting(name, value)
    return problem


def effective_settings():
    """On-disk settings.json merged over the built-in defaults.

    The current value each tunable would take on the next run. Used by the GUI's
    Settings tab so it edits what is actually in effect, not only what has been
    written to the file so far.
    """
    # settings.json was already applied over the defaults at import, so the
    # module's current values are exactly what the next run will use.
    return current_settings()


def save_settings(data, path=None):
    """Write a settings mapping to settings.json, keeping a doc comment."""
    path = Path(path or SETTINGS_JSON)
    payload = {
        "_comment": (
            "Values used by solid_maker.py. Delete any line to fall back to the "
            "built-in default. Sizes are millimetres."
        ),
        **data,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_settings_at_import():
    """Apply settings.json before anything reads the constants."""
    try:
        data = load_settings()
    except ValueError as error:
        print(f"  ! Ignoring settings: {error}")
        return
    if not data:
        return
    problems = apply_settings(data)
    for problem in problems:
        print(f"  ! settings.json: {problem}")
    applied = len(data) - len(problems)
    if applied > 0:
        print(f"Applied {applied} setting(s) from {SETTINGS_JSON.name}.")


# Apply settings here, before the functions below are defined: several take the
# constants as default arguments, which Python evaluates at definition time.
_load_settings_at_import()


def scad_header() -> str:
    """SCAD preamble.

    `use <font.ttf>` loads the font straight from the repo. Without it OpenSCAD
    silently falls back to a default font when "Dumbledor 1 Fixed" is not
    installed system-wide: the tokens still render, they just look wrong, with
    no warning at all. Loading the file directly removes that failure mode and
    means nobody has to install the font on Windows.

    Forward slashes are deliberate. OpenSCAD accepts them on Windows, whereas
    a backslash is an escape character inside a SCAD string.
    """
    return f"use <{FONT_FILE.as_posix()}>;\n$fn=100;"


# --------------------------------------------------------------------------
# External tool discovery
# --------------------------------------------------------------------------
_OPENSCAD_CACHE: str | None = None


def find_openscad() -> str:
    """Locate the OpenSCAD executable.

    The Windows installer does not add OpenSCAD to PATH, so `shutil.which` is
    not enough on its own; we also probe the standard install locations. Set
    the OPENSCAD_PATH environment variable to override.
    """
    global _OPENSCAD_CACHE
    if _OPENSCAD_CACHE:
        return _OPENSCAD_CACHE

    candidates: list[str] = []

    override = os.environ.get("OPENSCAD_PATH")
    if override:
        candidates.append(override)

    found = shutil.which("openscad")
    if found:
        candidates.append(found)

    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        candidates += [
            r"C:\Program Files\OpenSCAD\openscad.exe",
            r"C:\Program Files (x86)\OpenSCAD\openscad.exe",
            str(Path(local) / "Programs" / "OpenSCAD" / "openscad.exe"),
        ]
    else:
        candidates += [
            "/usr/bin/openscad",
            "/usr/local/bin/openscad",
            "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD",
        ]

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            _OPENSCAD_CACHE = candidate
            return candidate

    raise FileNotFoundError(
        "Could not find OpenSCAD.\n"
        "Install it (Windows: winget install --id OpenSCAD.OpenSCAD), then add "
        "it to PATH or set OPENSCAD_PATH. In PowerShell:\n"
        '  $env:OPENSCAD_PATH = "C:\\Program Files\\OpenSCAD\\openscad.exe"'
    )


# --------------------------------------------------------------------------
# Text measurement
# --------------------------------------------------------------------------
def get_relative_widths_pillow(font_path, font_size, characters):
    """Relative widths of characters in a proportional font, via Pillow.

    Args:
        font_path (str): Path to the font file (e.g. .ttf, .otf).
        font_size (int): Font size in points.
        characters (str): Characters to measure.

    Returns:
        dict: Mapping of character -> width.
    """
    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        print(f"Error: Font file not found at {font_path}")
        return {}

    widths = {}
    for char in characters:
        width, _height = font.getbbox(char)[2:4]  # width from the bounding box
        widths[char] = width
    return widths


_REFERENCE_FONT_PX = 100


@lru_cache(maxsize=1)
def _reference_font():
    """The token font at a fixed size, loaded once.

    Reminder layout measures every candidate line at every trial size, so
    re-reading the .ttf from disk on each call dominated layout time.
    """
    return ImageFont.truetype(str(FONT_FILE), _REFERENCE_FONT_PX)


def scad_text(value, **kwargs):
    """OpenSCAD text() with the string safely escaped.

    SolidPython writes strings into the .scad file verbatim, so a role name or
    reminder containing a double quote would end the string early and the rest
    would run as OpenSCAD code (e.g. an import() of some local file). Names come
    from scraped wiki data and from other people's script files, so they are
    not trusted. Ordinary names have nothing to escape, so their .scad output
    -- and the build cache keyed on it -- is unchanged.
    """
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    # A raw line break or other control character has no business on a token
    # and would also split the SCAD string, so drop them.
    escaped = "".join(ch for ch in escaped if ch.isprintable())
    return text(escaped, **kwargs)


def _text_width_mm(line: str, size_mm: float) -> float:
    """Advance width of `line`, in mm, when rendered at `size_mm`.

    Font advance scales linearly with size, so measure once at a large
    reference size and scale the result.
    """
    if not line:
        return 0.0
    return _reference_font().getlength(line) / _REFERENCE_FONT_PX * size_mm


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
def felt_coin_model():
    """The base character-token disc."""
    return cylinder(d=COIN_DIAMETER, h=COIN_HEIGHT)


def reminder_base_model():
    """The base reminder-token disc."""
    return cylinder(d=REMINDER_DIAMETER, h=REMINDER_HEIGHT)


def _flatten_svg_path(d, bezier_samples=6):
    """Parse the traced SVG path 'd' into a list of subpaths (each a list of
    (x, y) points), flattening cubic beziers to short line segments.

    We build the art from explicit points rather than letting OpenSCAD import
    the SVG, because OpenSCAD's SVG importer flips and offsets the Y axis (the
    art lands low, drifting toward the name). Owning the coordinates keeps the
    art reliably centred on every OpenSCAD version.
    """

    def cubic(p0, p1, p2, p3, t):
        u = 1 - t
        return (
            u * u * u * p0[0]
            + 3 * u * u * t * p1[0]
            + 3 * u * t * t * p2[0]
            + t * t * t * p3[0],
            u * u * u * p0[1]
            + 3 * u * u * t * p1[1]
            + 3 * u * t * t * p2[1]
            + t * t * t * p3[1],
        )

    tokens = d.replace(",", " ").split()
    subpaths, current = [], []
    pos = start = (0.0, 0.0)
    i = 0
    while i < len(tokens):
        cmd = tokens[i]
        i += 1
        if cmd == "M":
            if current:
                subpaths.append(current)
            pos = start = (float(tokens[i]), float(tokens[i + 1]))
            current = [pos]
            i += 2
        elif cmd == "L":
            pos = (float(tokens[i]), float(tokens[i + 1]))
            current.append(pos)
            i += 2
        elif cmd == "C":
            c1 = (float(tokens[i]), float(tokens[i + 1]))
            c2 = (float(tokens[i + 2]), float(tokens[i + 3]))
            end = (float(tokens[i + 4]), float(tokens[i + 5]))
            for s in range(1, bezier_samples + 1):
                current.append(cubic(pos, c1, c2, end, s / bezier_samples))
            pos = end
            i += 6
        elif cmd == "Z":
            if current:
                subpaths.append(current)
                current = []
            pos = start
        else:
            i += 1  # unknown token; skip defensively
    if current:
        subpaths.append(current)
    return subpaths


def _icon_2d(svg_filename, target_diameter: float):
    """Build the role art as a 2D polygon, centred on the origin and scaled so
    its larger dimension is `target_diameter` mm.

    A single traced SVG serves both the character token and the smaller reminder
    token; only the scale differs.
    """
    svg_text = Path(svg_filename).read_text(encoding="utf-8")
    match = re.search(r'\bd="([^"]+)"', svg_text)
    if not match:
        raise ValueError(f"No path found in {svg_filename}")
    subpaths = _flatten_svg_path(match.group(1))

    xs = [x for sp in subpaths for x, _ in sp]
    ys = [y for sp in subpaths for _, y in sp]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    side = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
    factor = target_diameter / side

    # Centre on the art's bounding box and flip Y (SVG is Y-down, OpenSCAD is
    # Y-up) so the art comes out upright and centred on the disc.
    points, paths = [], []
    for sp in subpaths:
        indices = []
        for x, y in sp:
            indices.append(len(points))
            points.append([(x - cx) * factor, (cy - y) * factor])
        paths.append(indices)
    return polygon(points=points, paths=paths)


def _fit_role_icon_diameter(char_points, shift, max_d, clearance):
    """Largest icon diameter (<= max_d) whose circle, centred at (0, shift),
    stays `clearance` mm clear of every glyph inner-edge point.

    Short names leave the art at max_d; long names (which curve further up the
    sides) shrink it just enough to avoid crowding the name.
    """
    max_r = max_d / 2
    for px, py in char_points:
        max_r = min(max_r, math.hypot(px - 0.0, py - shift) - clearance)
    return max(2 * max_r, 0.0)


def role_overlay_model(role_name, svg_filename):
    """Character-token overlay: role art plus the name curved around the rim."""
    # --- Curved text ---
    # 5x the text size was found by the original author, by trial and error, to
    # convert text pixels into angular distance nicely.
    printed_role_name = role_name.upper()
    relative_widths = get_relative_widths_pillow(
        str(FONT_FILE), int(TEXT_SIZE * 5), printed_role_name
    )
    relative_widths[" "] = 14  # fixed width for the space character

    radius = COIN_DIAMETER / 2 - TEXT_BASELINE_INSET  # bring the text in from edge
    text_angle = 270  # start at the bottom

    # Measure the upper-cased text throughout: upper() can change the length
    # ("ß" -> "SS"), and the glyphs placed below are the upper-cased ones.
    average_char_width = sum(relative_widths[c] for c in printed_role_name) / len(
        printed_role_name
    )
    total_angle = (len(printed_role_name) - 1) * average_char_width
    start_angle = text_angle - total_angle / 2

    # First pass: work out each glyph's angle along the curve.
    char_angles = []
    char_angle = start_angle
    previous_width = 0
    for char in printed_role_name:
        if previous_width != 0:
            char_angle += (previous_width + relative_widths[char]) / 2.0
        char_angles.append(char_angle)
        previous_width = relative_widths[char]

    # Fit the art so it never collides with the name. The glyph inner edge sits
    # ~TEXT_SIZE inward of the baseline radius; long names reach round the sides,
    # so this trims the art for them while short names keep the full size.
    inner_radius = radius - TEXT_SIZE
    inner_points = [
        (
            inner_radius * math.cos(math.radians(a)),
            inner_radius * math.sin(math.radians(a)),
        )
        for a in char_angles
    ]
    if svg_filename is None:
        centered_svg = None  # text-only token (custom tokens may have no art)
    else:
        icon_diameter = _fit_role_icon_diameter(
            inner_points, ROLE_ICON_SHIFT_Y, ROLE_ICON_DIAMETER, ROLE_ICON_CLEARANCE
        )
        centered_svg = _icon_2d(svg_filename, icon_diameter)

    # Build the whole overlay as one 2D profile (icon + every glyph) and extrude
    # it a single time. Extruding each glyph separately and unioning ~a dozen 3D
    # solids in CGAL is exact-arithmetic and slow (tens of seconds per token);
    # unioning 2D shapes and extruding once is near-instant and identical output.
    # The art sits just above centre, in the space the bottom-curving name leaves
    # free, and is fitted (above) to stay clear of that name.
    profile_parts = []
    if centered_svg is not None:
        profile_parts.append(translate((0, ROLE_ICON_SHIFT_Y, 0))(centered_svg))
    for char, char_angle in zip(printed_role_name, char_angles, strict=True):
        x = radius * math.cos(math.radians(char_angle))
        y = radius * math.sin(math.radians(char_angle))

        character = scad_text(
            char,
            font=FONT,
            size=TEXT_SIZE,
            halign="center",
            valign="bottom",
        )
        # +90 so each glyph's "up" points outward from the centre. rotate about
        # Z and translate keep the glyph a 2D shape in the plane.
        placed = translate((x, y, 0))(rotate(a=char_angle + 90, v=[0, 0, 1])(character))
        profile_parts.append(placed)

    profile = union()(*profile_parts)
    return translate((0, 0, COIN_HEIGHT - ROLE_EXTRUDE_DEPTH))(
        linear_extrude(height=ROLE_EXTRUDE_DEPTH)(profile)
    )


def reminder_baseline_y(
    index: int, size: float, diameter: float = REMINDER_DIAMETER
) -> float:
    """Baseline y of a text line, counting up from the bottom line (index 0)."""
    usable_radius = diameter / 2 - 1.5
    bottom = -usable_radius * REMINDER_TEXT_BASELINE_FRACTION
    return bottom + index * size * 1.2


def reminder_text_top_y(
    lines: list[str], size: float, diameter: float = REMINDER_DIAMETER
) -> float:
    """Top of the text block: the cap height above the topmost baseline."""
    return reminder_baseline_y(len(lines) - 1, size, diameter) + size


def layout_reminder_text(
    label: str,
    max_size: float = REMINDER_TEXT_SIZE,
    min_size: float = REMINDER_TEXT_MIN_SIZE,
    diameter: float = REMINDER_DIAMETER,
    max_lines: int = REMINDER_MAX_LINES,
) -> tuple[list[str], float]:
    """Wrap and auto-shrink reminder text so it fits inside a round token.

    Reminder labels vary a lot in length ("Dead" vs "Minions not nominated"),
    and the usable width shrinks toward the bottom of the disc. Pick the fewest
    lines at the largest font size that still fits within the chord width at
    each line's lowest point.

    Returns:
        (lines, font_size_mm)
    """
    label = " ".join(label.upper().split())
    usable_radius = diameter / 2 - 1.5  # keep clear of the rim

    def fits(lines: list[str], size: float) -> bool:
        for index, line in enumerate(reversed(lines)):
            y = reminder_baseline_y(index, size, diameter)
            if abs(y) >= usable_radius:
                return False
            half_chord = math.sqrt(max(usable_radius**2 - y**2, 0.0))
            if _text_width_mm(line, size) / 2.0 > half_chord:
                return False
        return reminder_text_top_y(lines, size, diameter) < usable_radius

    for line_count in range(1, max_lines + 1):
        if line_count == 1:
            candidates = [[label]]
        else:
            # textwrap works in characters, so sweep the width to find splits
            # that land on exactly `line_count` lines. break_long_words must be
            # off, or a narrow width hacks words apart ("HERRING" -> "HE"/"RRING").
            candidates = []
            for width in range(len(label), 2, -1):
                wrapped = textwrap.wrap(
                    label,
                    width=width,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                if len(wrapped) == line_count and wrapped not in candidates:
                    candidates.append(wrapped)

        size = max_size
        while size >= min_size:
            for lines in candidates:
                if fits(lines, size):
                    return lines, round(size, 2)
            size -= 0.1

    # Nothing fitted, so fall back to the smallest size and accept it is tight.
    fallback = textwrap.wrap(
        label, width=12, break_long_words=False, break_on_hyphens=False
    )[:max_lines]
    return (fallback or [label]), min_size


def reminder_overlay_model(reminder_label, svg_filename):
    """Reminder-token overlay: role art on top, straight wrapped text below.

    Real reminder tokens use straight rather than curved text, so we match that.
    """
    lines, size = layout_reminder_text(reminder_label)

    usable_radius = REMINDER_DIAMETER / 2 - 1.5
    z = REMINDER_HEIGHT - ROLE_EXTRUDE_DEPTH

    # Assemble the whole overlay as a single 2D profile: art plus every text
    # line, each translated in the plane. Keeping this in 2D is the important
    # part -- see the clip note below.
    profile_parts = []
    text_shift_y = 0.0

    if svg_filename is None:
        # Text-only token (custom tokens may have no art): the text block would
        # otherwise sit low, where it was making room for art, so centre it.
        block_bottom = reminder_baseline_y(0, size)
        block_top = reminder_text_top_y(lines, size)
        text_shift_y = -(block_bottom + block_top) / 2.0
    else:
        # Fit the art into whatever vertical space the text left above it. Two-line
        # labels therefore get a smaller icon rather than one that collides.
        icon_bottom = reminder_text_top_y(lines, size) + REMINDER_ICON_TEXT_GAP
        icon_diameter = min(
            REMINDER_DIAMETER * REMINDER_ICON_MAX_FRACTION,
            usable_radius - icon_bottom,
        )
        icon_center_y = icon_bottom + icon_diameter / 2

        icon = _icon_2d(svg_filename, icon_diameter)
        if REMINDER_ICON_DILATE > 0:
            # Fatten thin strokes so they survive at reminder scale and print in
            # the second colour. Offsetting before the disc clip keeps art off
            # the rim.
            icon = offset(r=REMINDER_ICON_DILATE)(icon)
        profile_parts.append(translate((0, icon_center_y, 0))(icon))

    for index, line in enumerate(reversed(lines)):
        glyphs = scad_text(
            line,
            font=FONT,
            size=size,
            halign="center",
            valign="baseline",
        )
        profile_parts.append(
            translate((0, reminder_baseline_y(index, size) + text_shift_y, 0))(glyphs)
        )

    profile = union()(*profile_parts)

    # Clip to the token footprint so nothing overhangs the disc. This is done in
    # 2D (intersect with a circle) on purpose: a 3D intersection of the extruded
    # art + glyphs against a cylinder is an exact-arithmetic CGAL boolean that
    # can take minutes or effectively hang OpenSCAD on finely traced artwork. A
    # 2D polygon clip is near-instant, and we extrude the result just once.
    clipped = intersection()(profile, circle(d=REMINDER_DIAMETER))
    return translate((0, 0, z))(linear_extrude(height=ROLE_EXTRUDE_DEPTH)(clipped))


# --------------------------------------------------------------------------
# "Choose a ..." draft tokens
# --------------------------------------------------------------------------
# Full-size (45 mm) tokens used in draft formats like Whale's Buffet, where a
# player picks any role of a type from the script rather than being dealt one.
# They belong to no role and no script, so they are generated only on request.
# Each is a text-only character token in its team's colour. The real tokens show
# the phrase on straight centred lines (not curved round the rim like a role
# name), because "CHOOSE A TOWNSFOLK" is too long to curve legibly.
CHOOSE_TOKENS = (
    # (label, team-colour name)
    ("Choose a Townsfolk", "blue"),
    ("Choose an Outsider", "blue"),
    ("Choose a Minion", "red"),
    ("Choose a Demon", "red"),
)
CHOOSE_TEXT_DILATE = 0.08  # merge touching glyph contours; also aids printing
CHOOSE_RIM_CLEARANCE = 2.5  # keep draft-token text this far in from the disc rim


def choose_token_definitions():
    """The built-in draft tokens as role entries, keyed by label.

    Same shape as a roles.json entry (text-only, so no ``image``), so they flow
    through the checklist and plates like any other character token.
    """
    return {
        label: {
            "image": None,
            "color": colour,
            "team": "townsfolk" if colour == "blue" else "minion",
            "reminders": [],
            "remindersGlobal": [],
            "_source": "choose",
        }
        for label, colour in CHOOSE_TOKENS
    }


def choose_a_overlay_model(label):
    """Full-size character-token overlay: the phrase on straight centred lines.

    Reuses the reminder text layout -- which wraps and auto-shrinks to fit a
    round token -- but at coin scale and a larger font, so a draft token matches
    the look of the real printed ones.
    """
    # Lay the text out as though the disc were a few mm smaller, so it keeps
    # well clear of the rim. Letters that reach exactly to the edge get clipped
    # by the disc intersection, and that clip is what left a non-manifold edge
    # where a glyph met the rim on the short, wide words ("DEMON", "MINION").
    layout_diameter = COIN_DIAMETER - CHOOSE_RIM_CLEARANCE * 2
    lines, size = layout_reminder_text(
        label,
        max_size=TEXT_SIZE * 1.4,
        min_size=TEXT_SIZE * 0.6,
        diameter=layout_diameter,
        max_lines=3,
    )

    # Centre the whole block vertically (same approach as a text-only reminder).
    block_bottom = reminder_baseline_y(0, size, layout_diameter)
    block_top = reminder_text_top_y(lines, size, layout_diameter)
    text_shift_y = -(block_bottom + block_top) / 2.0

    profile_parts = []
    for index, line in enumerate(reversed(lines)):
        glyphs = scad_text(
            line, font=FONT, size=size, halign="center", valign="baseline"
        )
        profile_parts.append(
            translate(
                (0, reminder_baseline_y(index, size, layout_diameter) + text_shift_y, 0)
            )(glyphs)
        )

    # A hair of dilation merges glyph contours that only touch. Without it two
    # neighbouring letters can meet at a single edge, which becomes a
    # non-manifold edge once the text is subtracted from the disc (seen on the
    # short, large "DEMON"/"MINION"). It also helps the text print cleanly.
    profile = offset(r=CHOOSE_TEXT_DILATE)(union()(*profile_parts))
    # Clip to a circle inside the rim, not the rim itself: a glyph meeting the
    # disc edge exactly leaves a coincident, non-manifold edge after the pocket
    # is subtracted. The text is already laid out to stay inside this.
    clipped = intersection()(profile, circle(d=COIN_DIAMETER - CHOOSE_RIM_CLEARANCE))
    z = COIN_HEIGHT - ROLE_EXTRUDE_DEPTH
    return translate((0, 0, z))(linear_extrude(height=ROLE_EXTRUDE_DEPTH)(clipped))


def build_choose_token(
    label, data, skip_stl=False, make_3mf=True, art_on_bottom=True, cache=None
):
    """Emit one full-size "Choose a ..." draft token."""
    name_safe = safe_name(label)
    colour = data.get("color", "unknown")
    colour_safe = safe_name(colour)
    _emit_overlay(
        choose_a_overlay_model(label),
        scad_file=SCAD_DIR / f"{name_safe}_coin_overlay.scad",
        stl_file=STL_DIR / f"{colour_safe}_{name_safe}_coin_overlay.stl",
        threemf_file=THREEMF_DIR / f"{colour_safe}_{name_safe}_coin.3mf",
        base_stl=STL_DIR / f"{COIN_BASE_NAME}.stl",
        colour=colour,
        skip_stl=skip_stl,
        make_3mf=make_3mf,
        art_on_bottom=art_on_bottom,
        cache=cache,
    )


# --------------------------------------------------------------------------
# Image pipeline
# --------------------------------------------------------------------------
# Role art is a few hundred KB. The cap stops a bad or hostile URL (homebrew
# characters in a downloaded script file carry their own image link) from
# filling the disk or memory.
MAX_ART_DOWNLOAD_BYTES = 20 * 1024 * 1024


def download_png(url, filename):
    """Download role art from `url` and save it to `filename`.

    Only http(s) is fetched, the body is size-capped, and it must decode as an
    image before anything is written, so a wrong or malicious link fails here
    with a clear message instead of landing on disk and confusing the tracer.
    """
    if not _looks_like_url(str(url)):
        raise RuntimeError(f"Refusing to download non-http(s) art URL: {url}")
    response = requests.get(url, timeout=30, stream=True)
    if response.status_code != 200:
        raise RuntimeError(f"Failed to download {url} (HTTP {response.status_code})")
    body = bytearray()
    for chunk in response.iter_content(chunk_size=64 * 1024):
        body.extend(chunk)
        if len(body) > MAX_ART_DOWNLOAD_BYTES:
            raise RuntimeError(
                f"Art at {url} is over {MAX_ART_DOWNLOAD_BYTES // (1024 * 1024)} MB; "
                "refusing to download it."
            )
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.verify()
    except Exception as error:  # Pillow raises many types for bad data
        raise RuntimeError(f"{url} did not return a valid image: {error}") from error
    Path(filename).write_bytes(bytes(body))
    print(f"Downloaded {filename}")


def convert_png_to_greyscale_png(png_path, greyscale_png_path):
    """Flatten a PNG onto white (dropping transparency), then convert to grey."""
    image = Image.open(png_path).convert("RGBA")
    background = Image.new("RGBA", image.size, (255, 255, 255))
    composite = Image.alpha_composite(background, image)
    greyscale = composite.convert("L")
    greyscale.save(greyscale_png_path)
    print(f"Converted {png_path} to {greyscale_png_path}")


def convert_to_svg_with_potrace(png_path, svg_path, size_mm=COIN_DIAMETER):
    """Trace a greyscale PNG to SVG using pure-Python potrace.

    The original shelled out to ImageMagick's `convert` plus the `potrace`
    binary. On Windows, `convert` collides with the built-in
    C:\\Windows\\System32\\convert.exe (the FAT-to-NTFS conversion tool), so
    that call is not merely broken there, it is the wrong program. Tracing
    in-process removes both external binaries.
    """
    image = Image.open(png_path).convert("L")
    width, height = image.size

    # NOTE: potracer's Bitmap inverts its input internally, so it must be given
    # the greyscale image (bright = background). Handing it a pre-thresholded
    # "ink" mask traces the background instead and yields inverted artwork.
    bitmap = potrace.Bitmap(np.array(image), blacklevel=0.5)
    path = bitmap.trace(turdsize=2)

    subpaths = []
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def track(x, y):
        nonlocal min_x, min_y, max_x, max_y
        min_x, max_x = min(min_x, x), max(max_x, x)
        min_y, max_y = min(min_y, y), max(max_y, y)

    for curve in path:
        start = curve.start_point
        track(start.x, start.y)
        commands = [f"M {start.x:.3f} {start.y:.3f}"]
        for segment in curve:
            end = segment.end_point
            if segment.is_corner:
                track(segment.c.x, segment.c.y)
                track(end.x, end.y)
                commands.append(
                    f"L {segment.c.x:.3f} {segment.c.y:.3f} L {end.x:.3f} {end.y:.3f}"
                )
            else:
                track(segment.c1.x, segment.c1.y)
                track(segment.c2.x, segment.c2.y)
                track(end.x, end.y)
                commands.append(
                    f"C {segment.c1.x:.3f} {segment.c1.y:.3f} "
                    f"{segment.c2.x:.3f} {segment.c2.y:.3f} "
                    f"{end.x:.3f} {end.y:.3f}"
                )
        commands.append("Z")
        subpaths.append(" ".join(commands))

    # Crop to a square around the art's bounding box (with a small margin) and
    # centre it. The source art carries a lot of transparent margin; without
    # this crop the traced shape only fills part of its box, so it prints much
    # smaller than the token allows -- especially on the little reminder tokens.
    if min_x <= max_x and min_y <= max_y:
        art_w = max_x - min_x
        art_h = max_y - min_y
        side = max(art_w, art_h) * 1.06 or float(max(width, height))
        cx = (min_x + max_x) / 2.0
        cy = (min_y + max_y) / 2.0
        view = f"{cx - side / 2:.3f} {cy - side / 2:.3f} {side:.3f} {side:.3f}"
    else:  # no paths traced; fall back to the full image
        view = f"0 0 {width} {height}"

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{size_mm}mm" height="{size_mm}mm" '
        f'viewBox="{view}">\n'
        f'  <path d="{" ".join(subpaths)}" fill="black" fill-rule="evenodd"/>\n'
        "</svg>\n"
    )
    Path(svg_path).write_text(svg, encoding="utf-8")
    print(f"Traced {png_path} to {svg_path}")


@lru_cache(maxsize=1)
def openscad_render_flags():
    """Extra CLI flags worth passing to this OpenSCAD build.

    OpenSCAD 2023 and later ship the Manifold geometry engine, which is far
    faster than the old CGAL path for models built from many boolean cuts, such
    as engraved text. Older builds don't know the flag, so it is only used
    when advertised.
    """
    try:
        help_text = subprocess.run(
            [find_openscad(), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError, SystemExit):
        return []
    blob = f"{help_text.stdout}\n{help_text.stderr}".lower()
    if "--backend" in blob and "manifold" in blob:
        return ["--backend=manifold"]
    return []


def export_coin_to_stl(
    model, scad_filename="coin.scad", stl_filename="coin.stl", timeout=180
):
    """Render a .scad file to .stl with the OpenSCAD CLI.

    A per-token timeout means one pathological piece of geometry can't freeze the
    whole batch: it reports the failure and the caller moves on to the next role.
    """
    flags = openscad_render_flags()
    try:
        result = _run_openscad(flags, scad_filename, stl_filename, timeout)
        # If this build advertised the flag but won't take it, don't lose the
        # render: retry once with the default backend.
        if result.returncode != 0 and flags and _rejected_option(result.stderr):
            print("  ! OpenSCAD rejected --backend=manifold; retrying without it.")
            result = _run_openscad([], scad_filename, stl_filename, timeout)
    except subprocess.TimeoutExpired:
        print(
            f"[TIMEOUT] OpenSCAD took over {timeout}s on {scad_filename}; "
            f"skipping {stl_filename}. The .scad file was still written."
        )
        return

    if result.returncode == 0:
        print(f"[ok] STL exported: {stl_filename}")
        return
    print(f"[ERROR] Failed to generate {stl_filename}")
    print(result.stderr)


def _run_openscad(flags, scad_filename, stl_filename, timeout):
    """One OpenSCAD render. Raises subprocess.TimeoutExpired on timeout."""
    return subprocess.run(
        [find_openscad(), *flags, "-o", str(stl_filename), str(scad_filename)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _rejected_option(stderr):
    """True when OpenSCAD's error output says it didn't understand a flag."""
    stderr = (stderr or "").lower()
    return any(
        hint in stderr
        for hint in ("unknown option", "unrecognised", "unrecognized", "invalid option")
    )


# --------------------------------------------------------------------------
# Two-colour 3MF packaging
# --------------------------------------------------------------------------
_STL_VERTEX_RE = re.compile(
    r"vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)", re.ASCII
)


def _load_ascii_stl(path):
    """Read an ASCII STL into (vertices Nx3, faces Mx3) with shared vertices.

    OpenSCAD writes ASCII STL, which is triangle soup: every triangle carries
    its own three vertices, so neighbours are duplicated. 3MF wants an indexed
    mesh, so we de-duplicate (rounding to swallow floating-point noise) and
    rebuild the face indices.
    """
    text = Path(path).read_text()
    coords = np.array(_STL_VERTEX_RE.findall(text), dtype=np.float64)
    if coords.size == 0 or coords.shape[0] % 3 != 0:
        raise ValueError(f"{path}: expected a positive multiple of 3 vertices")
    flat = coords.reshape(-1, 3)
    _, first_index, inverse = np.unique(
        np.round(flat, 5), axis=0, return_index=True, return_inverse=True
    )
    vertices = flat[first_index]
    faces = inverse.reshape(-1, 3)
    return vertices, faces


_COLORGROUP_ID = "1"


def _face_coloured_mesh_xml(bodies):
    """Build one <mesh> merging several bodies, each triangle tagged with a face
    colour.

    ``bodies`` is a list of (vertices, faces, colour_index). Every triangle gets
    ``pid``/``p1`` pointing into a colour group, i.e. 3MF *face colouring* --
    which is what Bambu Studio's "Standard 3MF Color Parsing" actually reads
    (object-level base materials are ignored by that importer, so the colours
    would otherwise not show up).
    """
    vert_chunks = []
    tri_chunks = []
    offset = 0
    for vertices, faces, colour_index in bodies:
        vert_chunks.append(
            "".join(
                f'<vertex x="{x:.5f}" y="{y:.5f}" z="{z:.5f}"/>' for x, y, z in vertices
            )
        )
        tri_chunks.append(
            "".join(
                f'<triangle v1="{a + offset}" v2="{b + offset}" v3="{c + offset}" '
                f'pid="{_COLORGROUP_ID}" p1="{colour_index}"/>'
                for a, b, c in faces
            )
        )
        offset += len(vertices)
    verts = "".join(vert_chunks)
    tris = "".join(tri_chunks)
    return f"<mesh><vertices>{verts}</vertices><triangles>{tris}</triangles></mesh>"


def _recess_disc_for_inlay(base_v, base_f, overlay_v, overlay_f):
    """Carve the overlay footprint out of the top of the disc.

    Returns (vertices, faces) for the disc with an art-shaped pocket, so the
    coloured inlay drops in flush. BOTC tokens are drawn blind from a bag, so
    the printed face must be smooth -- a raised relief could be identified by
    touch. Flush therefore needs a real boolean (disc minus overlay), which
    manifold3d does robustly in well under a millisecond.
    """
    try:
        from manifold3d import Manifold, Mesh
    except ImportError as exc:  # pragma: no cover - only if dep missing
        raise RuntimeError(
            "manifold3d is required for the flush two-colour 3MF export. "
            "Install it with `uv sync` (or `uv pip install manifold3d`), "
            "or pass --no-3mf to generate STLs only."
        ) from exc

    def to_manifold(vertices, faces):
        return Manifold(
            Mesh(
                vert_properties=np.asarray(vertices, dtype=np.float32),
                tri_verts=np.asarray(faces, dtype=np.uint32),
            )
        )

    recessed = to_manifold(base_v, base_f) - to_manifold(overlay_v, overlay_f)
    mesh = recessed.to_mesh()
    verts = np.asarray(mesh.vert_properties)[:, :3].astype(np.float64)
    faces = np.asarray(mesh.tri_verts).astype(np.int64)
    return verts, faces


def _flip_art_to_bottom(vertices, height):
    """Turn the token over so the coloured face prints against the build plate.

    The two-colour face comes out cleanest as the first layer on the bed, which
    hides any height/texture mismatch between the filaments. This is a true 180
    degree rotation about the X axis (x, y, z) -> (x, -y, height - z), not a
    mirror, so text stays readable rather than coming out backwards.
    """
    flipped = vertices.copy()
    flipped[:, 1] = -flipped[:, 1]
    flipped[:, 2] = height - flipped[:, 2]
    return flipped


def _token_bodies(base_stl, overlay_stl, art_on_bottom=True):
    """Load a token and return its two watertight bodies (disc, inlay).

    Shared by the single-token 3MF and the multi-token plate packer, so both
    produce geometry the same way.
    """
    base_v, base_f = _load_ascii_stl(base_stl)
    over_v, over_f = _load_ascii_stl(overlay_stl)
    disc_v, disc_f = _recess_disc_for_inlay(base_v, base_f, over_v, over_f)
    if art_on_bottom:
        height = float(base_v[:, 2].max())  # disc top face = COIN/REMINDER height
        disc_v = _flip_art_to_bottom(disc_v, height)
        over_v = _flip_art_to_bottom(over_v, height)
    return (disc_v, disc_f), (over_v, over_f)


def _token_radius(vertices):
    """In-plane radius of a token, for spacing it out on a plate."""
    return float(np.max(np.hypot(vertices[:, 0], vertices[:, 1])))


_3MF_CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
_3MF_MAT_NS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"


def _write_3mf_package(out_path, model_xml):
    """Zip a 3dmodel.model string into a valid .3mf package."""
    rels_ct = "application/vnd.openxmlformats-package.relationships+xml"
    model_ct = "application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        f'<Default Extension="rels" ContentType="{rels_ct}"/>'
        f'<Default Extension="model" ContentType="{model_ct}"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>'
        "</Relationships>"
    )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("3D/3dmodel.model", model_xml)


def write_two_colour_3mf(
    out_path,
    base_stl,
    overlay_stl,
    overlay_color_hex,
    base_color_hex=BASE_COLOR_HEX,
    art_on_bottom=True,
):
    """Combine a base disc and its overlay into one flush two-colour 3MF.

    The overlay STL is authored inset into the top 0.2 mm of the disc, so the
    two bodies share that volume. Rather than lift the overlay into a raised
    relief (which would be tactile -- bad for tokens drawn blind from a bag),
    the overlay footprint is subtracted from the disc to leave a shallow pocket,
    and the overlay drops into it. The result is a smooth, flat top with two
    colours meeting in-plane, and two disjoint watertight bodies that slice
    cleanly.

    With ``art_on_bottom`` (the default) the whole token is turned over so the
    coloured face prints against the plate for the cleanest colour boundary.
    """
    (disc_v, disc_f), (over_v, over_f) = _token_bodies(
        base_stl, overlay_stl, art_on_bottom
    )

    # Face colouring via the materials extension: a colour group holding the two
    # colours, then every triangle references one by index. Bambu Studio's colour
    # parser reads this (it ignores object-level base materials).
    colorgroup = (
        f'<m:colorgroup id="{_COLORGROUP_ID}">'
        f'<m:color color="{base_color_hex}"/>'
        f'<m:color color="{overlay_color_hex}"/>'
        "</m:colorgroup>"
    )
    # Keep the disc and inlay as two independently watertight bodies (merging
    # them into one mesh would double the pocket walls and make it non-manifold).
    # Each body is uniformly face-coloured, then grouped under one assembly so
    # the token still drags in as a single item.
    disc = (
        '<object id="2" type="model" name="disc">'
        f"{_face_coloured_mesh_xml([(disc_v, disc_f, 0)])}</object>"
    )
    inlay = (
        '<object id="3" type="model" name="inlay">'
        f"{_face_coloured_mesh_xml([(over_v, over_f, 1)])}</object>"
    )
    assembly = (
        '<object id="4" type="model"><components>'
        '<component objectid="2"/><component objectid="3"/>'
        "</components></object>"
    )
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<model unit="millimeter" xml:lang="en-US" xmlns="{_3MF_CORE_NS}" '
        f'xmlns:m="{_3MF_MAT_NS}">'
        f"<resources>{colorgroup}{disc}{inlay}{assembly}</resources>"
        '<build><item objectid="4"/></build>'
        "</model>"
    )
    _write_3mf_package(out_path, model)
    print(f"[ok] 3MF exported: {out_path}")


def overlay_colour_hex(color_name):
    """Map a role's colour to a 3MF display colour.

    Accepts an official team-colour name ("red", "blue", ...) or, for custom
    tokens, an explicit "#RRGGBB" / "#RRGGBBAA" hex value.
    """
    if not isinstance(color_name, str):
        return DEFAULT_OVERLAY_COLOR_HEX
    value = color_name.strip()
    if value.startswith("#"):
        digits = value[1:]
        if re.fullmatch(r"[0-9a-fA-F]{6}", digits):
            return f"#{digits.upper()}FF"
        if re.fullmatch(r"[0-9a-fA-F]{8}", digits):
            return f"#{digits.upper()}"
        return DEFAULT_OVERLAY_COLOR_HEX
    return OVERLAY_COLOR_BY_NAME.get(value.lower(), DEFAULT_OVERLAY_COLOR_HEX)


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def _looks_like_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def has_art(image_spec):
    """False for a text-only token, whose ``image`` is absent or empty."""
    return image_spec not in (None, "", False)


def resolve_role_image(image_spec, base_dir):
    """Normalise a role's ``image`` field.

    Returns a URL unchanged, or resolves a local path (relative to the JSON file
    that declared it, then to the project root) to an absolute path. ``None``
    means the token has no art -- a text-only token.
    """
    if not has_art(image_spec):
        return None
    image_spec = str(image_spec)
    if _looks_like_url(image_spec):
        return image_spec
    candidate = Path(image_spec).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    for root in (Path(base_dir), PROJECT_ROOT):
        resolved = (root / candidate).resolve()
        if resolved.exists():
            return str(resolved)
    # Report the most useful guess; the caller validates existence.
    return str((Path(base_dir) / candidate).resolve())


def validate_role_entry(name, entry):
    """Return a list of human-readable problems with one custom role entry."""
    problems = []
    if not str(name).strip():
        problems.append("role name is empty")
    if not isinstance(entry, dict):
        return [f"{name!r}: entry must be an object"]

    colour = entry.get("color", "unknown")
    known = set(OVERLAY_COLOR_BY_NAME)
    if not isinstance(colour, str) or (
        colour.lower() not in known
        and colour.lower() != "unknown"
        and not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", colour.strip())
    ):
        problems.append(
            f"{name!r}: color {colour!r} is not one of "
            f"{sorted(known)} or a #RRGGBB value"
        )

    for field in ("reminders", "remindersGlobal"):
        value = entry.get(field, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            problems.append(f"{name!r}: {field} must be a list of strings")

    image = entry.get("image")
    if (
        has_art(image)
        and not _looks_like_url(str(image))
        and not Path(str(image)).exists()
    ):
        problems.append(f"{name!r}: image file not found: {image}")
    return problems


def load_role_definitions(roles_json=ROLES_JSON, custom_jsons=()):
    """Load official roles then merge custom ones on top.

    Custom entries use the same schema as roles.json, with two conveniences:
    ``image`` may be a local file path (resolved relative to the custom JSON),
    and may be omitted entirely for a text-only token. A custom entry with the
    same name as an official role replaces it.
    """
    roles = {}
    if roles_json and Path(roles_json).exists():
        roles.update(load_json_file(roles_json))

    problems = []
    for path in map(Path, custom_jsons):
        if not path.exists():
            problems.append(f"custom roles file not found: {path}")
            continue
        custom = load_json_file(path)
        if not isinstance(custom, dict):
            problems.append(f"{path}: top level must be an object of role -> data")
            continue
        for name, entry in custom.items():
            if name.startswith("_"):
                continue  # JSON has no comments; allow "_comment" keys as docs
            if not isinstance(entry, dict):
                problems.append(f"{path}: {name!r} must be an object")
                continue
            merged = dict(entry)
            merged["image"] = resolve_role_image(entry.get("image"), path.parent)
            merged.setdefault("color", "unknown")
            merged.setdefault("reminders", [])
            merged.setdefault("remindersGlobal", [])
            merged["_source"] = "custom"
            problems.extend(validate_role_entry(name, merged))
            roles[name] = merged

    if problems:
        raise ValueError(
            "Problems in custom role definitions:\n  - " + "\n  - ".join(problems)
        )

    # Fold in any extra reminders last, so they attach to official and custom
    # roles alike. An unmatched name is surfaced as a warning rather than an
    # error: it should not stop a build, but you want to know about the typo.
    try:
        unmatched = apply_extra_reminders(roles)
    except ValueError as error:
        print(f"  ! {error}")
    else:
        for name in unmatched:
            print(f"  ! extra_reminders.json: no role matching {name!r}; skipped.")
    return roles


def analyse_art(grey_png_path, smallest_diameter_mm=None):
    """Analyse one greyscale art image. Returns (warnings, stats).

    Pure: no printing, so it can drive a batch pre-flight report as well as the
    inline check. ``stats`` carries the ink fraction and the thin-feature loss.
    """
    if smallest_diameter_mm is None:
        smallest_diameter_mm = REMINDER_DIAMETER * REMINDER_ICON_MAX_FRACTION

    problems = []
    image = Image.open(grey_png_path).convert("L")
    pixels = np.asarray(image)
    ink = pixels < 128
    fraction = float(ink.mean())
    thin_loss = 0.0

    if fraction < ART_MIN_INK_FRACTION:
        problems.append(
            f"art is {fraction:.1%} ink - nearly blank. Check the source image "
            "is solid dark artwork (transparency is flattened onto white)."
        )
    elif fraction > ART_MAX_INK_FRACTION:
        problems.append(
            f"art is {fraction:.1%} ink - nearly solid. A dark background will "
            "trace as one big blob; use art on a transparent or white background."
        )
    else:
        # Erode by the smallest printable feature, measured at reminder scale.
        px_per_mm = max(image.size) / float(smallest_diameter_mm)
        radius_px = max(round(ART_MIN_FEATURE_MM / 2 * px_per_mm), 1)
        kernel = 2 * radius_px + 1
        if kernel < min(image.size):
            # The art is dark ink on a light background, so taking the local
            # MAXIMUM lightens/shrinks the ink (erosion). MinFilter would grow
            # it instead, which silently never trips the check.
            eroded = image.filter(ImageFilter.MaxFilter(kernel))
            remaining = float((np.asarray(eroded) < 128).mean())
            thin_loss = 1.0 - (remaining / fraction if fraction else 0.0)
            if thin_loss > ART_THIN_FEATURE_LOSS:
                problems.append(
                    f"{thin_loss:.0%} of the art is finer than {ART_MIN_FEATURE_MM} mm "
                    f"at reminder-token size. Thin strokes tend to disappear when "
                    "printed; consider bolder artwork."
                )

    stats = {"ink_fraction": fraction, "thin_loss": thin_loss}
    return problems, stats


def check_art_quality(grey_png_path, smallest_diameter_mm=None):
    """Warn about art that is unlikely to trace or print well.

    Catches the two failure modes seen in practice: an image that is nearly
    blank (wrong file, or art that vanished when flattened) and one whose
    strokes are so fine they disappear at reminder-token scale. Returns the list
    of warnings and prints them; it never raises, since it is only advisory.
    """
    problems, _stats = analyse_art(grey_png_path, smallest_diameter_mm)
    for problem in problems:
        print(f"  ! {Path(grey_png_path).name}: {problem}")
    return problems


def load_scripts(scripts_json=SCRIPTS_JSON):
    """Load named scripts/editions: {"Trouble Brewing": ["Washerwoman", ...]}.

    Returns an empty dict when the file is absent. Keys beginning with _ are
    skipped so the file can carry _comment documentation.
    """
    path = Path(scripts_json)
    if not path.exists():
        return {}
    data = load_json_file(path)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be an object of script -> roles")

    scripts = {}
    for name, roles in data.items():
        if name.startswith("_"):
            continue
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise ValueError(f"{path}: {name!r} must be a list of role names")
        scripts[name] = roles
    return scripts


def script_for_role(role, scripts):
    """Names of the scripts containing `role`, joined for display."""
    wanted = normalise_role_name(role)
    found = [
        name
        for name, roles in scripts.items()
        if any(normalise_role_name(r) == wanted for r in roles)
    ]
    return ", ".join(sorted(found))


def add_extra_reminder(role, label, extra_json=None):
    """Append one reminder label for a role to extra_reminders.json.

    Returns True if it was added, False if that role already had that exact
    label. Existing entries are matched loosely, so adding to "Fortune Teller"
    lands on an existing "fortuneteller" key rather than making a second one.
    """
    label = str(label).strip()
    if not label:
        raise ValueError("reminder text is empty")

    extra_json = Path(EXTRA_REMINDERS_JSON if extra_json is None else extra_json)
    data = {}
    if extra_json.exists():
        loaded = load_json_file(extra_json)
        if not isinstance(loaded, dict):
            raise ValueError(
                f"{extra_json.name}: expected an object of role -> list of reminders"
            )
        data = loaded

    # Reuse an existing key for the same role, however it was spelled.
    target_key = str(role)
    for existing_key in data:
        if not str(existing_key).startswith("_") and normalise_role_name(
            existing_key
        ) == normalise_role_name(role):
            target_key = existing_key
            break

    labels = list(data.get(target_key, []))
    if label in labels:
        return False
    labels.append(label)
    data[target_key] = labels

    extra_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def apply_extra_reminders(roles, extra_json=None):
    """Add reminders from extra_reminders.json onto matching roles, in place.

    The file maps a role name to a list of reminder labels. Names are matched
    loosely (so "Fortune Teller", "fortuneteller" and "Fortune-Teller" all
    land on the same role), and a label already present is not duplicated. This
    is the easy way to give the Harpy the reminder the wiki forgot, or to add a
    custom reminder to any role -- official or homebrew -- without rewriting it.

    Returns the list of role names that were not found, so a typo is reported
    rather than silently doing nothing.
    """
    extra_json = Path(EXTRA_REMINDERS_JSON if extra_json is None else extra_json)
    if not extra_json.exists():
        return []

    data = load_json_file(extra_json)
    if not isinstance(data, dict):
        raise ValueError(
            f"{extra_json.name}: expected an object of role -> list of reminders"
        )

    by_key = {normalise_role_name(name): name for name in roles}
    unmatched = []
    for role_name, labels in data.items():
        if str(role_name).startswith("_"):
            continue  # allow "_comment" documentation keys
        if not isinstance(labels, list) or not all(
            isinstance(label, str) for label in labels
        ):
            raise ValueError(
                f"{extra_json.name}: reminders for {role_name!r} must be a list "
                "of strings"
            )
        target = by_key.get(normalise_role_name(role_name))
        if target is None:
            unmatched.append(str(role_name))
            continue
        existing = list(roles[target].get("reminders", []))
        for label in (raw.strip() for raw in labels):
            if label and label not in existing:
                existing.append(label)
        roles[target]["reminders"] = existing
    return unmatched


def normalise_role_name(name):
    """Loose role-name key, so "Snake Charmer" matches "snakecharmer"."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def filter_roles_by_script(roles, script_name, scripts):
    """Restrict `roles` to the members of `script_name` (case-insensitive)."""
    matches = [n for n in scripts if n.lower() == script_name.strip().lower()]
    if not matches:
        # A near-miss is nearly always a typo, and eyeballing two similar names
        # in a list is exactly what people are bad at.
        close = difflib.get_close_matches(script_name, list(scripts), n=3, cutoff=0.6)
        suggestion = f" Did you mean {close[0]!r}?" if close else ""
        raise KeyError(
            f"No script named {script_name!r}.{suggestion} "
            f"Available: {sorted(scripts) or 'none'}"
        )
    wanted = {normalise_role_name(r): r for r in scripts[matches[0]]}
    selected = {k: v for k, v in roles.items() if normalise_role_name(k) in wanted}
    missing = sorted(
        wanted[key] for key in set(wanted) - {normalise_role_name(k) for k in selected}
    )
    if missing:
        # Name them: the cause is nearly always a typo in scripts.json or a role
        # that has not been scraped yet, and neither is fixable from a count.
        print(
            f"  ! {matches[0]}: no definition for {', '.join(missing[:8])}"
            f"{'...' if len(missing) > 8 else ''} - skipped. Check the spelling "
            f"in {SCRIPTS_JSON.name}, or add the role to {CUSTOM_ROLES_JSON.name}."
        )
    return selected


def parse_script_file(path):
    """Read an official script-creator / script-hosting JSON file.

    Both shapes that those tools export are accepted:

        [{"id": "_meta", "name": "Catfishing", ...}, "investigator", "chef"]
        [{"id": "_meta", ...}, {"id": "investigator"}, {"id": "chef"}]

    Entries may also be full homebrew character definitions carrying their own
    name/team/ability/reminders, which is how custom characters travel inside a
    script; those are returned separately.

    Returns (name, author, role_ids, homebrew) where homebrew is a list of the
    full character objects.
    """
    data = load_json_file(path)
    if not isinstance(data, list):
        raise ValueError(
            f"{path}: expected a script file - a JSON list of characters, "
            f"got {type(data).__name__}. Is this a scripts.json instead?"
        )

    name = Path(path).stem
    author = ""
    role_ids = []
    homebrew = []

    for entry in data:
        if isinstance(entry, str):
            role_ids.append(entry)
            continue
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id", ""))
        if entry_id == "_meta":
            name = entry.get("name") or name
            author = entry.get("author") or ""
            continue
        if not entry_id:
            continue
        role_ids.append(entry_id)
        # A bare {"id": ...} just references an official role; anything richer
        # is a homebrew character defined inline.
        if set(entry) - {"id"}:
            homebrew.append(entry)

    if not role_ids:
        raise ValueError(f"{path}: no characters found in this script file")
    return name, author, role_ids, homebrew


def team_colour_map(roles):
    """Work out which colour each team uses, from the roles already scraped.

    Deriving it beats hardcoding a table, because the colours come from the wiki
    and this way homebrew characters inherit whatever convention roles.json
    already uses.
    """
    tally = {}
    for data in roles.values():
        team = str(data.get("team", "")).lower()
        colour = data.get("color")
        if team and colour and colour != "unknown":
            tally.setdefault(team, Counter())[colour] += 1
    return {team: counts.most_common(1)[0][0] for team, counts in tally.items()}


def homebrew_to_role_entry(entry, colours):
    """Convert an inline script character into a custom_roles.json entry."""
    team = str(entry.get("team", "")).lower()
    reminders = [str(r) for r in entry.get("reminders", []) if str(r).strip()]
    globals_ = [str(r) for r in entry.get("remindersGlobal", []) if str(r).strip()]
    return {
        "image": entry.get("image") or None,
        "color": colours.get(team, "unknown"),
        "reminders": reminders,
        "remindersGlobal": globals_,
    }


BUILD_CACHE_JSON = PROJECT_ROOT / ".build_cache.json"
# Bump when the 3MF writer's output changes in a way the .scad file cannot
# capture (colour handling, orientation, packaging), so cached 3MFs rebuild.
THREEMF_FORMAT_VERSION = 3

_SCAD_TIMESTAMP_RE = re.compile(r"^// Generated by SolidPython.*$", re.MULTILINE)


def write_scad(model, scad_file, header=None):
    """Write a .scad file and return its geometry fingerprint.

    The fingerprint ignores the generation timestamp, so an unchanged model
    fingerprints identically run to run. Because the art is emitted as inline
    polygon coordinates, the .scad text captures everything that determines the
    STL: artwork, names, sizes, and any geometry code change. That makes it the
    natural thing to cache on.

    `header` overrides the preamble, which is how a caller picks a different
    curve resolution: $fn=100 is right for a token's disc but ruinous for
    engraved text, where every glyph curve gets a hundred segments.
    """
    scad_render_to_file(
        model,
        str(scad_file),
        file_header=scad_header() if header is None else header,
        # Don't append a copy of this script to every .scad; it made each file
        # ~1600 lines of noise and meant unrelated edits looked like changes.
        include_orig_code=False,
    )
    text = Path(scad_file).read_text(encoding="utf-8")
    stripped = _SCAD_TIMESTAMP_RE.sub("", text)
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


class BuildCache:
    """Remembers what each output was built from, so unchanged work is skipped.

    Rendering with OpenSCAD is the slow part of a run, and most runs change only
    a role or two. Each output records a fingerprint of its inputs; if that
    matches and the file is still on disk, the render is skipped.
    """

    VERSION = 2

    def __init__(self, path=BUILD_CACHE_JSON, enabled=True):
        self.path = Path(path)
        self.enabled = enabled
        self.entries = {}
        self.skipped = 0
        self.built = 0
        if enabled and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("version") == self.VERSION:
                    self.entries = dict(data.get("entries", {}))
            except (OSError, json.JSONDecodeError):
                self.entries = {}  # a corrupt cache should never break a build

    def _key(self, output):
        try:
            return str(Path(output).resolve().relative_to(PROJECT_ROOT))
        except ValueError:
            return str(Path(output).resolve())

    def is_current(self, output, fingerprint):
        """True when `output` exists and was built from this fingerprint."""
        if not self.enabled:
            return False
        if self.entries.get(self._key(output)) != fingerprint:
            return False
        return Path(output).exists()

    def record(self, output, fingerprint):
        self.entries[self._key(output)] = fingerprint
        self.built += 1

    def skip(self):
        self.skipped += 1

    def forget(self, output):
        self.entries.pop(self._key(output), None)

    def save(self):
        if not self.enabled:
            return
        try:
            self.path.write_text(
                json.dumps(
                    {"version": self.VERSION, "entries": self.entries}, indent=1
                ),
                encoding="utf-8",
            )
        except OSError as error:
            print(f"  ! Could not write {self.path.name}: {error}")


def threemf_fingerprint(scad_fingerprint, colour_hex, art_on_bottom):
    """Fingerprint for a 3MF: the geometry plus everything else that shapes it."""
    parts = [
        scad_fingerprint,
        colour_hex,
        str(bool(art_on_bottom)),
        BASE_COLOR_HEX,
        str(THREEMF_FORMAT_VERSION),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def render_stl_if_needed(model, scad_file, stl_file, fingerprint, cache):
    """Render to STL unless an identical one was already built."""
    if cache.is_current(stl_file, fingerprint):
        cache.skip()
        return False
    export_coin_to_stl(model, scad_file, stl_file)
    if Path(stl_file).exists():
        cache.record(stl_file, fingerprint)
    else:
        # A failed or timed-out render must not be remembered as current.
        cache.forget(stl_file)
    return True


def write_3mf_if_needed(
    out_path,
    base_stl,
    overlay_stl,
    overlay_color_hex,
    scad_fingerprint,
    cache,
    art_on_bottom=True,
):
    """Write the two-colour 3MF unless an identical one was already built."""
    fingerprint = threemf_fingerprint(
        scad_fingerprint, overlay_color_hex, art_on_bottom
    )
    if cache.is_current(out_path, fingerprint):
        cache.skip()
        return False
    write_two_colour_3mf(
        out_path,
        base_stl,
        overlay_stl,
        overlay_color_hex,
        art_on_bottom=art_on_bottom,
    )
    if Path(out_path).exists():
        cache.record(out_path, fingerprint)
    return True


def safe_name(value: str) -> str:
    """Filesystem-safe token name (Windows forbids \\ / : * ? " < > | )."""
    value = value.strip().replace("'", "").replace("\u2019", "")
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "_" for c in value)
    # Collapse runs so "Lil' Monsta" becomes Lil_Monsta, not Lil__Monsta.
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_") or "unnamed"


# Base discs are shared by every token of a size, printed in the felt colour.
COIN_BASE_NAME = "000_coin_base_2mm_felt"
REMINDER_BASE_NAME = "000_reminder_base_2mm_felt"


def role_reminders(data):
    """Every reminder label a role owns, seated and global, duplicates kept.

    A repeated label means you need several copies of that token, so callers
    decide whether to count or collapse them.
    """
    return list(data.get("reminders") or []) + list(data.get("remindersGlobal") or [])


def prepare_role_art(role, image_spec):
    """Fetch or copy a role's art and flatten it to greyscale.

    Returns the greyscale PNG path. Shared by the build and the art pre-flight,
    so both judge exactly the same pixels.
    """
    role_safe = safe_name(role)
    png_file = PNG_DIR / f"{role_safe}.png"
    grey_file = GREY_PNG_DIR / f"{role_safe}.png"
    if _looks_like_url(str(image_spec)):
        if not png_file.exists():
            download_png(image_spec, png_file)
    else:
        # Custom art on disk: copy it in so the pipeline is unchanged and the
        # source file is never modified.
        source = Path(image_spec)
        if not source.exists():
            raise FileNotFoundError(f"art file not found: {source}")
        if source.resolve() != png_file.resolve():
            shutil.copy(source, png_file)
    convert_png_to_greyscale_png(png_file, grey_file)
    return grey_file


def _emit_overlay(
    model,
    *,
    scad_file,
    stl_file,
    threemf_file,
    base_stl,
    colour,
    skip_stl,
    make_3mf,
    art_on_bottom,
    cache,
):
    """Write one overlay's .scad, then its STL and two-colour 3MF if wanted.

    Every token kind finishes the same way; only the model and file names
    differ. The 3MF needs both STLs, so it is skipped if either render failed.
    """
    cache = cache or BuildCache(enabled=False)
    fingerprint = write_scad(model, scad_file)
    if skip_stl:
        return
    render_stl_if_needed(model, scad_file, stl_file, fingerprint, cache)
    if make_3mf and stl_file.exists() and base_stl.exists():
        write_3mf_if_needed(
            threemf_file,
            base_stl,
            stl_file,
            overlay_colour_hex(colour),
            fingerprint,
            cache,
            art_on_bottom=art_on_bottom,
        )


def build_character_token(
    role, data, skip_stl=False, make_3mf=True, art_on_bottom=True, cache=None
):
    """Trace the role art and emit the character-token overlay.

    Returns the traced SVG (reused for the role's reminders), or None for a
    text-only token.
    """
    role_safe = safe_name(role)
    image_spec = data.get("image")
    if has_art(image_spec):
        try:
            grey_file = prepare_role_art(role, image_spec)
        except FileNotFoundError as error:
            raise FileNotFoundError(f"{role}: {error}") from error
        check_art_quality(grey_file)
        svg_file = SVG_DIR / f"{role_safe}.svg"
        convert_to_svg_with_potrace(grey_file, svg_file)
    else:
        svg_file = None  # text-only token: name (and reminder text) but no art

    color = data.get("color", "unknown")
    color_safe = safe_name(color)  # custom roles may use a "#RRGGBB" colour
    _emit_overlay(
        role_overlay_model(role, svg_file),
        scad_file=SCAD_DIR / f"{role_safe}_coin_overlay.scad",
        stl_file=STL_DIR / f"{color_safe}_{role_safe}_coin_overlay.stl",
        threemf_file=THREEMF_DIR / f"{color_safe}_{role_safe}_coin.3mf",
        base_stl=STL_DIR / f"{COIN_BASE_NAME}.stl",
        colour=color,
        skip_stl=skip_stl,
        make_3mf=make_3mf,
        art_on_bottom=art_on_bottom,
        cache=cache,
    )
    return svg_file


def build_reminder_tokens(
    role, data, svg_file, skip_stl=False, make_3mf=True, art_on_bottom=True, cache=None
):
    """Emit one reminder-token overlay per reminder the role owns.

    Returns how many physical reminder tokens the role needs, counting repeats.
    """
    reminders = role_reminders(data)
    role_safe = safe_name(role)
    color = data.get("color", "unknown")
    color_safe = safe_name(color)  # custom roles may use a "#RRGGBB" colour
    # A role can list the same reminder several times (you print several copies
    # of one token); the files are identical, so only build it once.
    for reminder in dict.fromkeys(reminders):
        stem = f"{role_safe}_{safe_name(reminder)}_reminder"
        _emit_overlay(
            reminder_overlay_model(reminder, svg_file),
            scad_file=SCAD_DIR / f"{stem}.scad",
            stl_file=STL_DIR / f"{color_safe}_{stem}.stl",
            threemf_file=THREEMF_DIR / f"{color_safe}_{stem}.3mf",
            base_stl=STL_DIR / f"{REMINDER_BASE_NAME}.stl",
            colour=color,
            skip_stl=skip_stl,
            make_3mf=make_3mf,
            art_on_bottom=art_on_bottom,
            cache=cache,
        )
    return len(reminders)


# --------------------------------------------------------------------------
# Command-line helpers shared by every tool
# --------------------------------------------------------------------------
def default_custom_files(no_custom=False):
    """custom_roles.json when it exists, unless the run opted out of it."""
    if no_custom or not CUSTOM_ROLES_JSON.exists():
        return []
    return [CUSTOM_ROLES_JSON]


def load_selected_roles(
    custom_files, script=None, script_file=None, roles_json=None, scripts_json=None
):
    """The roles a run works on, narrowed to one script if asked.

    Returns (roles, scripts). A script file wins over a named script, matching
    every tool's --script-file/--script precedence. Raises ValueError, KeyError
    or OSError with a message fit to show the user. The JSON paths default to
    the module locations, resolved at call time so tests can redirect them.
    """
    roles = load_role_definitions(
        ROLES_JSON if roles_json is None else roles_json, custom_files
    )
    scripts = load_scripts(SCRIPTS_JSON if scripts_json is None else scripts_json)
    if script_file:
        name, _author, role_ids, _homebrew = parse_script_file(script_file)
        roles = filter_roles_by_script(roles, name, {name: role_ids})
    elif script:
        roles = filter_roles_by_script(roles, script, scripts)
    return roles, scripts


def parse_bed_size(text):
    """argparse type for a 'WIDTHxDEPTH' bed size in millimetres."""
    try:
        width, depth = (float(part) for part in text.lower().split("x"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            f"bed size must look like 256x256, got {text!r}"
        ) from error
    if width <= 0 or depth <= 0:
        raise argparse.ArgumentTypeError("bed dimensions must be positive")
    return width, depth


def _build_parser():
    parser = argparse.ArgumentParser(description="Generate BOTC token SCAD/STL files.")
    parser.add_argument(
        "--skip-stl",
        action="store_true",
        help="Only write .scad files (does not require OpenSCAD).",
    )
    parser.add_argument(
        "--no-reminders",
        action="store_true",
        help="Generate character tokens only.",
    )
    parser.add_argument(
        "--no-3mf",
        action="store_true",
        help="Skip the combined two-colour .3mf files (keep only STLs).",
    )
    parser.add_argument(
        "--write-settings",
        action="store_true",
        help=(
            f"Write {SETTINGS_JSON.name} containing every tunable at its current "
            f"value, as a starting point for tweaking, then exit."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Rebuild every STL and 3MF, ignoring the cache of unchanged files. "
            "Only needed if outputs were edited outside this tool."
        ),
    )
    parser.add_argument(
        "--art-up",
        action="store_true",
        help=(
            "Orient the 3MF tokens with the coloured art facing up. By default "
            "the art faces down, so it prints against the plate for the cleanest "
            "colour boundary."
        ),
    )
    parser.add_argument(
        "--only",
        metavar="ROLE",
        help="Generate a single role (case-insensitive), e.g. --only Poisoner",
    )
    parser.add_argument(
        "--script",
        metavar="NAME",
        help=(
            f"Only generate roles belonging to this script/edition, as defined "
            f"in {SCRIPTS_JSON.name}."
        ),
    )
    parser.add_argument(
        "--script-file",
        metavar="FILE",
        help=(
            "Generate the roles listed in an official script-creator JSON file "
            "(the .json you download from the script tool or a hosting site). "
            "No need to add it to scripts.json first."
        ),
    )
    parser.add_argument(
        "--custom",
        metavar="FILE",
        action="append",
        default=None,
        help=(
            f"Custom/homebrew role definitions, same schema as roles.json. "
            f"Defaults to {CUSTOM_ROLES_JSON.name} if present. May be repeated."
        ),
    )
    parser.add_argument(
        "--no-custom",
        action="store_true",
        help=f"Ignore {CUSTOM_ROLES_JSON.name} even if it exists.",
    )
    parser.add_argument(
        "--custom-only",
        action="store_true",
        help="Generate only the custom roles, skipping the official ones.",
    )
    parser.add_argument(
        "--choose-tokens",
        action="store_true",
        help=(
            'Also generate the four full-size "Choose a ..." draft tokens '
            "(Townsfolk/Outsider/Minion/Demon), for formats like Whale's Buffet. "
            "They belong to no script, so add this flag when you want them."
        ),
    )
    parser.add_argument(
        "--choose-only",
        action="store_true",
        help='Generate only the "Choose a ..." draft tokens and nothing else.',
    )
    return parser


def _select_roles(args, parser, custom_files):
    """Apply --custom-only/--script-file/--script/--only. Exits on a bad choice."""
    # "Choose a ..." draft tokens belong to no script. --choose-only makes just
    # those and skips loading roles.json entirely, so it works on a bare checkout.
    if args.choose_only:
        roles = {}
    else:
        base_json = None if args.custom_only else ROLES_JSON
        if not custom_files and not (base_json and base_json.exists()):
            parser.error(
                f"{ROLES_JSON.name} not found. Run: python get_all_roles.py "
                f"(or add {CUSTOM_ROLES_JSON.name} for custom tokens)"
            )

        try:
            roles = load_role_definitions(base_json, custom_files)
        except ValueError as error:
            parser.error(str(error))

    if custom_files:
        print(f"Loaded custom roles from: {', '.join(str(p) for p in custom_files)}")

    if args.script_file:
        try:
            script_name, author, role_ids, homebrew = parse_script_file(
                args.script_file
            )
            roles = filter_roles_by_script(roles, script_name, {script_name: role_ids})
        except (ValueError, KeyError, OSError) as error:
            parser.error(str(error).strip('"'))
        by_line = f" by {author}" if author else ""
        print(f"Script {script_name!r}{by_line}: {len(roles)} role(s)")
        if homebrew:
            print(
                f"  ! {len(homebrew)} homebrew character(s) are defined inside "
                f"that script file. Run import_script.py to add them to "
                f"{CUSTOM_ROLES_JSON.name}."
            )
        if not roles:
            parser.error(f"No known roles in {args.script_file}")

    if args.script:
        try:
            roles = filter_roles_by_script(roles, args.script, load_scripts())
        except (KeyError, ValueError) as error:
            parser.error(str(error))
        if not roles:
            parser.error(f"No known roles in script {args.script!r}")
        print(f"Script {args.script!r}: {len(roles)} role(s)")

    if args.only:
        wanted = args.only.strip().lower()
        roles = {k: v for k, v in roles.items() if k.lower() == wanted}
        if not roles:
            parser.error(f"No role matching {args.only!r} in {ROLES_JSON.name}")
    return roles


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.write_settings:
        if SETTINGS_JSON.exists():
            parser.error(
                f"{SETTINGS_JSON.name} already exists; delete or rename it first "
                "so your current tuning is not overwritten."
            )
        save_settings(current_settings())
        print(f"Wrote {SETTINGS_JSON} with {len(TUNABLE_SETTINGS) + 1} setting(s).")
        return

    if args.custom and not args.no_custom:
        custom_files = [Path(p) for p in args.custom]
    else:
        custom_files = default_custom_files(args.no_custom)
    roles = _select_roles(args, parser, custom_files)

    for directory in (PNG_DIR, GREY_PNG_DIR, SVG_DIR, SCAD_DIR, STL_DIR, THREEMF_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    if not args.skip_stl:
        find_openscad()  # fail fast, with a helpful message

    cache = BuildCache(enabled=not args.force)
    if args.force:
        print("Rebuilding everything (--force).")

    # Base discs: printed once each in the "felt" colour.
    bases = (
        (COIN_BASE_NAME, felt_coin_model()),
        (REMINDER_BASE_NAME, reminder_base_model()),
    )
    for name, model in bases:
        scad_file = SCAD_DIR / f"{name}.scad"
        stl_file = STL_DIR / f"{name}.stl"
        fingerprint = write_scad(model, scad_file)
        if not args.skip_stl:
            render_stl_if_needed(model, scad_file, stl_file, fingerprint, cache)

    make_3mf = not args.no_3mf and not args.skip_stl
    art_on_bottom = not args.art_up
    reminder_total = 0
    total_roles = len(roles)
    for index, (role, data) in enumerate(roles.items(), start=1):
        # A machine-readable progress line the GUI parses to drive its bar. It
        # reads naturally on the command line too, so it is not hidden noise.
        print(f"[progress] {index}/{total_roles} {role}", flush=True)
        svg_file = build_character_token(
            role,
            data,
            skip_stl=args.skip_stl,
            make_3mf=make_3mf,
            cache=cache,
            art_on_bottom=art_on_bottom,
        )
        if not args.no_reminders:
            reminder_total += build_reminder_tokens(
                role,
                data,
                svg_file,
                skip_stl=args.skip_stl,
                make_3mf=make_3mf,
                art_on_bottom=art_on_bottom,
                cache=cache,
            )

    choose_total = 0
    if args.choose_tokens or args.choose_only:
        choose = choose_token_definitions()
        for index, (label, data) in enumerate(choose.items(), start=1):
            print(f"[progress] {index}/{len(choose)} {label}", flush=True)
            build_choose_token(
                label,
                data,
                skip_stl=args.skip_stl,
                make_3mf=make_3mf,
                art_on_bottom=art_on_bottom,
                cache=cache,
            )
        choose_total = len(choose)

    cache.save()
    summary = (
        f"\nDone: {len(roles)} character token(s), {reminder_total} reminder token(s)."
    )
    if choose_total:
        summary += f' Plus {choose_total} "Choose a ..." token(s).'
    if cache.skipped:
        summary += (
            f"\nReused {cache.skipped} unchanged file(s); built {cache.built}. "
            f"Use --force to rebuild everything."
        )
    if make_3mf:
        summary += (
            f"\nTwo-colour 3MF files are in {THREEMF_DIR.name}/ - "
            "drag one into BambuStudio."
        )
    print(summary)


if __name__ == "__main__":
    # Windows consoles default to a legacy code page, so force UTF-8 and stop
    # a non-ASCII role name from raising UnicodeEncodeError mid-run.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
