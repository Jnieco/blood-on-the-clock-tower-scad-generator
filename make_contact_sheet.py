"""Render a one-page contact sheet of every token, to eyeball before printing.

    uv run python make_contact_sheet.py
    uv run python make_contact_sheet.py --script "Trouble Brewing"

Each token is drawn as its coloured disc with the traced art and the name (or
the reminder text) on top - a flat, top-down proof of what will print. It is a
quick way to spot a wrong image, an off-centre or too-faint icon, or a
mis-scaled name across the whole set at once, without opening files one by one.

The sheet is written to contact_sheet.png. This uses the traced SVGs the
generator already produces, so run the generator (or at least --skip-stl) first;
missing art is drawn as an empty disc so you can see what still needs tracing.
"""

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import solid_maker as sm

# On-screen sizes in pixels; the real millimetre proportions are preserved.
DISC_PX = 150
PAD = 14
COLS_DEFAULT = 6
LABEL_H = 22

_DISC_FILL = {
    "blue": (50, 121, 196),
    "red": (140, 14, 18),
    "purple": (128, 0, 128),
    "yellow": (170, 140, 40),
    "green": (63, 120, 81),
}
_DEFAULT_DISC = (110, 110, 120)


def _disc_colour(colour_name):
    if isinstance(colour_name, str) and colour_name.startswith("#"):
        try:
            digits = colour_name.lstrip("#")[:6]
            return tuple(int(digits[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return _DEFAULT_DISC
    return _DISC_FILL.get(str(colour_name).lower(), _DEFAULT_DISC)


def _load_font(size):
    try:
        return ImageFont.truetype(str(sm.FONT_FILE), size)
    except OSError:
        return ImageFont.load_default()


def _svg_polygons(svg_path):
    """Very small SVG path reader: returns filled polygons as pixel loops.

    The generator writes simple traced paths, so we only need M/L/C/Z handling
    with absolute and relative coords, sampling curves coarsely. This is for a
    preview, so approximate curves are fine.
    """
    try:
        tree = ET.parse(svg_path)
    except (ET.ParseError, OSError):
        return [], (0, 0, 1, 1)
    ns = {"svg": "http://www.w3.org/2000/svg"}
    polys = []
    for path in tree.iterfind(".//svg:path", ns):
        d = path.get("d", "")
        polys.extend(_parse_path(d))
    root = tree.getroot()
    # viewBox gives the coordinate bounds to fit into the disc.
    view = root.get("viewBox")
    if view:
        x0, y0, w, h = (float(v) for v in view.replace(",", " ").split())
        bounds = (x0, y0, x0 + w, y0 + h)
    else:
        pts = [p for poly in polys for p in poly]
        if pts:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bounds = (min(xs), min(ys), max(xs), max(ys))
        else:
            bounds = (0, 0, 1, 1)
    return polys, bounds


def _parse_path(d):
    """Turn an SVG path string into a list of point loops."""
    tokens = _tokenise_path(d)
    polys = []
    current = []
    pos = [0.0, 0.0]
    start = [0.0, 0.0]
    i = 0
    cmd = None
    while i < len(tokens):
        token = tokens[i]
        if isinstance(token, str):
            cmd = token
            i += 1
            if cmd in ("Z", "z"):
                if current:
                    current.append(tuple(start))
                    polys.append(current)
                    current = []
                continue
        rel = cmd.islower()
        c = cmd.upper()
        try:
            if c == "M":
                x, y = tokens[i], tokens[i + 1]
                i += 2
                if rel:
                    x += pos[0]
                    y += pos[1]
                if current:
                    polys.append(current)
                current = [(x, y)]
                pos = [x, y]
                start = [x, y]
                cmd = "l" if rel else "L"
            elif c == "L":
                x, y = tokens[i], tokens[i + 1]
                i += 2
                if rel:
                    x += pos[0]
                    y += pos[1]
                current.append((x, y))
                pos = [x, y]
            elif c == "C":
                x1, y1, x2, y2, x, y = tokens[i : i + 6]
                i += 6
                if rel:
                    x1 += pos[0]
                    y1 += pos[1]
                    x2 += pos[0]
                    y2 += pos[1]
                    x += pos[0]
                    y += pos[1]
                current.extend(_sample_cubic(pos, (x1, y1), (x2, y2), (x, y)))
                pos = [x, y]
            elif c in ("H", "V"):
                v = tokens[i]
                i += 1
                if c == "H":
                    x = v + (pos[0] if rel else 0)
                    current.append((x, pos[1]))
                    pos[0] = x
                else:
                    y = v + (pos[1] if rel else 0)
                    current.append((pos[0], y))
                    pos[1] = y
            else:
                i += 1  # skip anything we don't model
        except (IndexError, TypeError):
            break
    if current:
        polys.append(current)
    return polys


def _tokenise_path(d):
    tokens = []
    number = ""
    for ch in d:
        if ch.isalpha():
            if number:
                tokens.append(float(number))
                number = ""
            tokens.append(ch)
        elif ch in "-+" and number and number[-1] not in "eE":
            tokens.append(float(number))
            number = ch
        elif ch in " ,\t\n":
            if number:
                tokens.append(float(number))
                number = ""
        else:
            number += ch
    if number:
        tokens.append(float(number))
    return tokens


def _sample_cubic(p0, p1, p2, p3, steps=8):
    points = []
    for step in range(1, steps + 1):
        t = step / steps
        mt = 1 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        points.append((x, y))
    return points


def _draw_token(role, data, reminder=None):
    """Draw one token tile: coloured disc, art, and name/reminder text."""
    tile = Image.new("RGBA", (DISC_PX, DISC_PX), (255, 255, 255, 0))
    draw = ImageDraw.Draw(tile)
    colour = _disc_colour(data.get("color", "unknown"))
    draw.ellipse([2, 2, DISC_PX - 2, DISC_PX - 2], fill=colour)

    role_safe = sm.safe_name(role)
    svg = sm.SVG_DIR / f"{role_safe}.svg"
    if svg.exists():
        _draw_art(tile, svg)

    label = reminder if reminder is not None else role
    _draw_label(draw, label, reminder is not None)
    return tile


def _draw_art(tile, svg_path):
    polys, (x0, y0, x1, y1) = _svg_polygons(svg_path)
    if not polys:
        return
    span = max(x1 - x0, y1 - y0) or 1.0
    art_px = DISC_PX * 0.58
    scale = art_px / span
    ox = (DISC_PX - (x1 - x0) * scale) / 2 - x0 * scale
    oy = (DISC_PX - (y1 - y0) * scale) / 2 - y0 * scale - DISC_PX * 0.08

    art = Image.new("RGBA", tile.size, (255, 255, 255, 0))
    ad = ImageDraw.Draw(art)
    for poly in polys:
        if len(poly) >= 3:
            ad.polygon(
                [(px * scale + ox, py * scale + oy) for px, py in poly],
                fill=(20, 20, 20, 255),
            )
    tile.alpha_composite(art)


def _draw_label(draw, text, is_reminder):
    font = _load_font(16 if not is_reminder else 14)
    text = text if len(text) <= 18 else text[:17] + "\u2026"
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    x = (DISC_PX - w) / 2
    y = DISC_PX * 0.66
    # A light halo so the text reads over the disc colour.
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            draw.text((x + dx, y + dy), text, font=font, fill=(255, 255, 255, 230))
    draw.text((x, y), text, font=font, fill=(15, 15, 15, 255))


def collect_tiles(roles):
    """(role, data, reminder_or_None) for every token, characters then reminders."""
    tiles = []
    for role, data in sorted(roles.items(), key=lambda kv: kv[0].lower()):
        tiles.append((role, data, None))
        for label in dict.fromkeys(sm.role_reminders(data)):
            tiles.append((role, data, label))
    return tiles


def build_contact_sheet(roles, out_path=None, cols=COLS_DEFAULT):
    """Render every token to a grid image. Returns the output path."""
    out_path = Path(out_path or (sm.PROJECT_ROOT / "contact_sheet.png"))
    tiles = collect_tiles(roles)
    if not tiles:
        raise ValueError("No roles to draw.")

    cols = max(1, cols)
    rows = (len(tiles) + cols - 1) // cols
    cell = DISC_PX + PAD
    width = cols * cell + PAD
    height = rows * (cell + LABEL_H) + PAD + 40

    sheet = Image.new("RGB", (width, height), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    title_font = _load_font(22)
    draw.text(
        (PAD, 10),
        f"Token contact sheet - {len(tiles)} token(s)",
        font=title_font,
        fill=(30, 30, 30),
    )

    for index, (role, data, reminder) in enumerate(tiles):
        col = index % cols
        row = index // cols
        x = PAD + col * cell
        y = 40 + PAD + row * (cell + LABEL_H)
        tile = _draw_token(role, data, reminder)
        sheet.paste(tile, (x, y), tile)

    sheet.save(out_path)
    return out_path


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Render a contact sheet of every token."
    )
    parser.add_argument("--script", metavar="NAME", help="Only this script's tokens.")
    parser.add_argument(
        "--script-file", metavar="FILE", help="Only this script file's tokens."
    )
    parser.add_argument("--no-custom", action="store_true", help="Ignore custom roles.")
    parser.add_argument(
        "--cols", type=int, default=COLS_DEFAULT, help="Tokens per row (default 6)."
    )
    parser.add_argument(
        "--output", default=None, help="Output PNG (default: contact_sheet.png)."
    )
    args = parser.parse_args(argv)

    try:
        roles, _scripts = sm.load_selected_roles(
            sm.default_custom_files(args.no_custom),
            script=args.script,
            script_file=args.script_file,
        )
    except (ValueError, KeyError, OSError) as error:
        parser.error(str(error).strip('"'))

    try:
        out_path = build_contact_sheet(roles, out_path=args.output, cols=args.cols)
    except ValueError as error:
        parser.error(str(error))
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
