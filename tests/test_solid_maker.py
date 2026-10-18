import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from solid_maker import (
    REMINDER_DIAMETER,
    _load_ascii_stl,
    convert_png_to_greyscale_png,
    convert_to_svg_with_potrace,
    download_png,
    export_coin_to_stl,
    felt_coin_model,
    find_openscad,
    get_relative_widths_pillow,
    layout_reminder_text,
    overlay_colour_hex,
    reminder_base_model,
    reminder_overlay_model,
    role_overlay_model,
    safe_name,
    scad_header,
    write_two_colour_3mf,
)


@pytest.fixture
def test_image_path(tmp_path):
    """A simple white test image."""
    img_path = tmp_path / "test_image.png"
    Image.new("RGBA", (100, 100), color=(255, 255, 255, 255)).save(img_path)
    return img_path


@pytest.fixture
def icon_image_path(tmp_path):
    """An image with actual dark artwork in it, so there is something to trace."""
    img_path = tmp_path / "icon.png"
    img = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([50, 50, 150, 150], fill=(0, 0, 0, 255))
    img.save(img_path)
    return img_path


# ---------------------------------------------------------------- images


def test_convert_png_to_greyscale_png(test_image_path, tmp_path):
    output_path = tmp_path / "greyscale.png"
    convert_png_to_greyscale_png(test_image_path, output_path)

    assert os.path.exists(output_path)
    assert Image.open(output_path).mode == "L"  # L is PIL's greyscale mode


def test_convert_to_svg_with_potrace_traces_the_artwork(icon_image_path, tmp_path):
    """The trace runs in-process: no ImageMagick, no potrace binary."""
    svg_path = tmp_path / "icon.svg"
    convert_to_svg_with_potrace(icon_image_path, svg_path, size_mm=45)

    assert svg_path.exists()
    root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    assert root.get("width") == "45mm"

    paths = root.findall("{http://www.w3.org/2000/svg}path")
    assert len(paths) == 1
    assert paths[0].get("d").startswith("M ")


def test_trace_captures_the_shape_not_the_background(icon_image_path, tmp_path):
    """Regression: potracer inverts its input, so a naive ink mask traces the
    background and produces an inverted icon."""
    svg_path = tmp_path / "icon.svg"
    convert_to_svg_with_potrace(icon_image_path, svg_path)

    d = ET.fromstring(svg_path.read_text(encoding="utf-8"))[0].get("d")
    coords = [
        float(t)
        for t in d.replace("M", " ")
        .replace("C", " ")
        .replace("L", " ")
        .replace("Z", " ")
        .split()
    ]
    xs, ys = coords[0::2], coords[1::2]

    # The circle sits at 50..150 inside a 200x200 image. If we had traced the
    # background instead, the path would span the full 0..200 frame.
    assert min(xs) > 20 and max(xs) < 180
    assert min(ys) > 20 and max(ys) < 180


@patch("requests.get")
def test_download_png(mock_get, tmp_path):
    mock_get.return_value = MagicMock(status_code=200, content=b"test content")

    output_path = tmp_path / "test_download.png"
    download_png("https://example.com/test.png", output_path)

    assert os.path.exists(output_path)
    assert output_path.read_bytes() == b"test content"


# ---------------------------------------------------------------- text


@patch("PIL.ImageFont.truetype")
def test_get_relative_widths_pillow(mock_truetype, tmp_path):
    mock_font = MagicMock()
    mock_font.getbbox.return_value = (0, 0, 10, 20)
    mock_truetype.return_value = mock_font

    font_path = tmp_path / "test_font.ttf"
    font_path.write_text("")

    widths = get_relative_widths_pillow(font_path, 12, "ABC")

    assert widths == {"A": 10, "B": 10, "C": 10}
    mock_truetype.assert_called_once_with(font_path, 12)


@pytest.mark.parametrize(
    "label",
    ["Dead", "Poisoned", "Red herring", "Minions not nominated", "Is the Marionette"],
)
def test_layout_reminder_text_fits_inside_the_token(label):
    """Every real reminder label must fit within the disc."""
    from solid_maker import _text_width_mm

    lines, size = layout_reminder_text(label)

    assert 1 <= len(lines) <= 2
    assert size > 0
    # The text is upper-cased and no words are lost in wrapping.
    assert " ".join(lines) == label.upper()

    usable_radius = REMINDER_DIAMETER / 2 - 1.5
    for line in lines:
        assert _text_width_mm(line, size) <= 2 * usable_radius


def test_layout_reminder_text_shrinks_for_long_labels():
    _short_lines, short_size = layout_reminder_text("Dead")
    long_lines, long_size = layout_reminder_text("Minions not nominated")

    assert long_size <= short_size
    assert len(long_lines) > 1  # long labels must wrap


# ---------------------------------------------------------------- models


def test_felt_coin_model():
    model = felt_coin_model()
    assert model is not None
    assert "cylinder" in str(type(model))


def test_reminder_base_model_is_smaller_than_the_coin():
    from solid import scad_render

    from solid_maker import COIN_DIAMETER

    assert REMINDER_DIAMETER < COIN_DIAMETER
    assert f"d = {REMINDER_DIAMETER}" in scad_render(reminder_base_model())


_SQUARE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="45mm" height="45mm" '
    'viewBox="0 0 45 45"><path d="M 10 10 L 35 10 L 35 35 L 10 35 Z" '
    'fill="black"/></svg>'
)


def test_role_overlay_model(tmp_path):
    from solid import scad_render

    svg = tmp_path / "test.svg"
    svg.write_text(_SQUARE_SVG)

    result = role_overlay_model("Test Role", svg)

    assert result is not None
    # The art is now emitted as an explicit polygon (centred, no SVG import).
    assert "polygon" in scad_render(result)


def test_reminder_overlay_model_renders_label_text(tmp_path):
    from solid import scad_render

    svg = tmp_path / "test.svg"
    svg.write_text(_SQUARE_SVG)

    rendered = scad_render(reminder_overlay_model("Poisoned", svg))

    assert '"POISONED"' in rendered
    assert "polygon" in rendered  # art built as a polygon, not an import


def test_icon_2d_is_centred_on_the_origin(tmp_path):
    """The art polygon must be centred on (0, 0) regardless of where the traced
    path sits in the SVG, so it lands in the middle of the disc."""
    import re

    from solid import scad_render

    import solid_maker as sm

    # Path deliberately offset far from the origin (like a tightly-cropped trace).
    svg = tmp_path / "offset.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="100 200 40 40">'
        '<path d="M 100 200 L 140 200 L 140 240 L 100 240 Z" fill="black"/></svg>'
    )
    rendered = scad_render(sm._icon_2d(svg, 30.0))
    coords = [float(n) for n in re.findall(r"-?\d+\.?\d*", rendered.split("points")[1])]
    xs, ys = coords[0::2], coords[1::2]
    assert abs((min(xs) + max(xs)) / 2) < 1e-6
    assert abs((min(ys) + max(ys)) / 2) < 1e-6
    assert abs((max(xs) - min(xs)) - 30.0) < 1e-6  # scaled to target diameter


def test_scad_header_loads_the_font_from_disk():
    """Without `use <...ttf>` OpenSCAD silently substitutes a default font."""
    header = scad_header()
    assert header.startswith("use <")
    assert header.split("use <")[1].split(">")[0].endswith("Dumbledor1_fixed.ttf")
    assert "\\" not in header  # forward slashes only, even on Windows


# ---------------------------------------------------------------- platform


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Devil's Advocate", "Devils_Advocate"),
        ("Lil' Monsta", "Lil_Monsta"),
        ("Pit-Hag", "Pit-Hag"),
        ("Red herring", "Red_herring"),
    ],
)
def test_safe_name_strips_characters_windows_rejects(raw, expected):
    result = safe_name(raw)
    assert result == expected
    assert not set(result) & set('\\/:*?"<>|')


@patch("solid_maker.find_openscad", return_value="openscad")
@patch("subprocess.run")
def test_export_coin_to_stl(mock_run, _mock_find):
    mock_run.return_value = MagicMock(returncode=0)

    export_coin_to_stl("test_model", "test.scad", "test.stl")

    args, kwargs = mock_run.call_args
    assert args[0] == ["openscad", "-o", "test.stl", "test.scad"]
    # Decoding explicitly keeps Windows' non-UTF-8 console output from crashing.
    assert kwargs["encoding"] == "utf-8"


@patch("solid_maker.shutil.which", return_value=None)
@patch("pathlib.Path.is_file", return_value=False)
def test_find_openscad_raises_a_helpful_error(_mock_is_file, _mock_which, monkeypatch):
    monkeypatch.setattr("solid_maker._OPENSCAD_CACHE", None)
    monkeypatch.delenv("OPENSCAD_PATH", raising=False)

    with pytest.raises(FileNotFoundError, match="OPENSCAD_PATH"):
        find_openscad()


@patch("solid_maker.shutil.which", return_value=None)
def test_find_openscad_honours_the_env_override(_mock_which, monkeypatch, tmp_path):
    fake = tmp_path / "openscad.exe"
    fake.write_text("")

    monkeypatch.setattr("solid_maker._OPENSCAD_CACHE", None)
    monkeypatch.setenv("OPENSCAD_PATH", str(fake))

    assert find_openscad() == str(fake)


# --- Two-colour 3MF export ---------------------------------------------------

_CUBE_STL = """solid cube
facet normal 0 0 0
 outer loop
  vertex 0 0 0
  vertex 1 0 0
  vertex 1 1 0
 endloop
endfacet
facet normal 0 0 0
 outer loop
  vertex 0 0 0
  vertex 1 1 0
  vertex 0 1 0
 endloop
endfacet
endsolid cube
"""


def test_load_ascii_stl_deduplicates_vertices(tmp_path):
    stl = tmp_path / "cube.stl"
    stl.write_text(_CUBE_STL)

    vertices, faces = _load_ascii_stl(stl)

    # Two triangles share an edge, so there are four unique corners, not six.
    assert len(vertices) == 4
    assert faces.shape == (2, 3)
    assert faces.max() < len(vertices)


def test_overlay_colour_hex_maps_teams_and_falls_back():
    assert overlay_colour_hex("red") == "#8C0E12FF"
    assert overlay_colour_hex("blue") == "#3297F4FF"
    # Unknown team colours must still yield a valid hex, never crash.
    fallback = overlay_colour_hex("chartreuse")
    assert fallback.startswith("#") and len(fallback) == 9


def _write_box_stl(path, z0, z1, size=10.0):
    """Write a closed, watertight axis-aligned box STL spanning z0..z1.

    manifold3d requires manifold input, so the test fixtures must be real solids
    rather than a stray triangle.
    """
    h = size / 2.0
    corners = [
        (-h, -h, z0),
        (h, -h, z0),
        (h, h, z0),
        (-h, h, z0),
        (-h, -h, z1),
        (h, -h, z1),
        (h, h, z1),
        (-h, h, z1),
    ]
    # 12 triangles (2 per face) with outward winding.
    faces = [
        (0, 3, 2),
        (0, 2, 1),  # bottom
        (4, 5, 6),
        (4, 6, 7),  # top
        (0, 1, 5),
        (0, 5, 4),  # front
        (1, 2, 6),
        (1, 6, 5),  # right
        (2, 3, 7),
        (2, 7, 6),  # back
        (3, 0, 4),
        (3, 4, 7),  # left
    ]
    lines = ["solid box"]
    for a, b, c in faces:
        lines.append("facet normal 0 0 0\n outer loop")
        for idx in (a, b, c):
            x, y, z = corners[idx]
            lines.append(f"  vertex {x:.5f} {y:.5f} {z:.5f}")
        lines.append(" endloop\nendfacet")
    lines.append("endsolid box\n")
    path.write_text("\n".join(lines))


def test_write_two_colour_3mf_is_flush_and_two_colour(tmp_path):
    base = tmp_path / "base.stl"
    overlay = tmp_path / "overlay.stl"
    # A solid disc 0..2 and an inlay prism inset into its top 0.2 mm.
    _write_box_stl(base, 0.0, 2.0, size=10.0)
    _write_box_stl(overlay, 1.8, 2.0, size=3.0)
    out = tmp_path / "token.3mf"

    write_two_colour_3mf(out, base, overlay, "#8C0E12FF", art_on_bottom=False)

    assert out.exists()
    with zipfile.ZipFile(out) as archive:
        names = set(archive.namelist())
        model = archive.read("3D/3dmodel.model").decode()

    # A valid 3MF package has these three parts.
    assert {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"} <= names
    # Both colours are present in a colour group (face colouring, which Bambu
    # reads), and triangles reference them by index.
    assert "#2E2E2EFF" in model and "#8C0E12FF" in model
    assert "colorgroup" in model
    assert 'p1="0"' in model and 'p1="1"' in model
    # Flush, not raised: nothing pokes above the disc's top face at z = 2.0.
    zs = [float(z) for z in re.findall(r'z="([-\d.]+)"', model)]
    assert max(zs) == pytest.approx(2.0, abs=1e-4)
    # Well-formed XML with one build item.
    root = ET.fromstring(model)
    ns = {"c": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    assert len(root.findall(".//c:build/c:item", ns)) == 1


def _inlay_bounds_from_3mf(path):
    """Return (min_z, max_z) of the inlay object's vertices in a token 3MF."""
    core = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
    with zipfile.ZipFile(path) as archive:
        model = archive.read("3D/3dmodel.model").decode()
    root = ET.fromstring(model)
    ns = {"c": core}
    inlay = root.find('.//c:object[@name="inlay"]', ns)
    zs = [float(v.get("z")) for v in inlay.findall(".//c:vertex", ns)]
    return min(zs), max(zs)


def test_art_on_bottom_flips_inlay_to_the_plate(tmp_path):
    base = tmp_path / "base.stl"
    overlay = tmp_path / "overlay.stl"
    _write_box_stl(base, 0.0, 2.0, size=10.0)
    _write_box_stl(overlay, 1.8, 2.0, size=3.0)

    up = tmp_path / "up.3mf"
    down = tmp_path / "down.3mf"
    write_two_colour_3mf(up, base, overlay, "#8C0E12FF", art_on_bottom=False)
    write_two_colour_3mf(down, base, overlay, "#8C0E12FF", art_on_bottom=True)

    # Art up: inlay sits in the top 0.2 mm. Art down: it's flipped to the plate.
    assert _inlay_bounds_from_3mf(up) == pytest.approx((1.8, 2.0), abs=1e-4)
    assert _inlay_bounds_from_3mf(down) == pytest.approx((0.0, 0.2), abs=1e-4)


def test_flip_art_to_bottom_is_a_rotation_not_a_mirror():
    import numpy as np

    from solid_maker import _flip_art_to_bottom

    verts = np.array([[1.0, 2.0, 0.0], [-1.0, -3.0, 2.0]])
    flipped = _flip_art_to_bottom(verts, height=2.0)
    # (x, y, z) -> (x, -y, height - z): x preserved (no left-right mirror),
    # y negated and z inverted (the 180 deg rotation about X).
    assert np.allclose(flipped[:, 0], verts[:, 0])
    assert np.allclose(flipped[:, 1], -verts[:, 1])
    assert np.allclose(flipped[:, 2], [2.0, 0.0])


def test_role_icon_never_collides_with_the_name():
    """The fitted art must stay clear of every glyph on the curved name, for
    short names (full-size art) and long ones (art trimmed to fit)."""
    import math

    import solid_maker as sm

    def worst_clearance(name):
        rw = sm.get_relative_widths_pillow(
            str(sm.FONT_FILE), int(sm.TEXT_SIZE * 5), name.upper()
        )
        rw[" "] = 14
        radius = sm.COIN_DIAMETER / 2 - sm.TEXT_BASELINE_INSET
        avg = sum(rw[c] for c in name.upper()) / len(name)
        start = 270 - (len(name) - 1) * avg / 2
        ang, prev, angles = start, 0, []
        for ch in name.upper():
            if prev:
                ang += (prev + rw[ch]) / 2.0
            angles.append(ang)
            prev = rw[ch]
        inner = radius - sm.TEXT_SIZE
        pts = [
            (inner * math.cos(math.radians(a)), inner * math.sin(math.radians(a)))
            for a in angles
        ]
        d = sm._fit_role_icon_diameter(
            pts, sm.ROLE_ICON_SHIFT_Y, sm.ROLE_ICON_DIAMETER, sm.ROLE_ICON_CLEARANCE
        )
        r = d / 2
        return min(math.hypot(px, py - sm.ROLE_ICON_SHIFT_Y) - r for px, py in pts)

    for name in ["Empath", "Acrobat", "Poisoner", "Washerwoman", "Snakecharmer"]:
        assert worst_clearance(name) >= sm.ROLE_ICON_CLEARANCE - 1e-6, name


def test_role_icon_sits_above_centre_and_clears_the_name():
    """The art is nudged up into the empty top (never below centre) and still
    comes out a healthy size once fitted around the name."""
    import math

    import solid_maker as sm

    assert 0 <= sm.ROLE_ICON_SHIFT_Y <= 4  # a slight lift, not a big offset

    rw = sm.get_relative_widths_pillow(
        str(sm.FONT_FILE), int(sm.TEXT_SIZE * 5), "POISONER"
    )
    rw[" "] = 14
    radius = sm.COIN_DIAMETER / 2 - sm.TEXT_BASELINE_INSET
    avg = sum(rw[c] for c in "POISONER") / len("POISONER")
    start = 270 - (len("POISONER") - 1) * avg / 2
    ang, prev, angles = start, 0, []
    for ch in "POISONER":
        if prev:
            ang += (prev + rw[ch]) / 2.0
        angles.append(ang)
        prev = rw[ch]
    inner = radius - sm.TEXT_SIZE
    pts = [
        (inner * math.cos(math.radians(a)), inner * math.sin(math.radians(a)))
        for a in angles
    ]
    d = sm._fit_role_icon_diameter(
        pts, sm.ROLE_ICON_SHIFT_Y, sm.ROLE_ICON_DIAMETER, sm.ROLE_ICON_CLEARANCE
    )
    assert 29.0 <= d <= sm.ROLE_ICON_DIAMETER  # sizable, never over the cap


# ---------------------------------------------------------------------------
# Custom / homebrew tokens
# ---------------------------------------------------------------------------
def _write_png(path, draw_fn, size=(400, 400)):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", size, (255, 255, 255, 0))
    draw_fn(ImageDraw.Draw(image))
    image.save(path)
    return path


def test_custom_roles_are_merged_and_can_override(tmp_path):
    import solid_maker as sm

    base = tmp_path / "roles.json"
    base.write_text(
        json.dumps(
            {
                "Poisoner": {
                    "image": "https://example.com/p.png",
                    "color": "red",
                    "reminders": ["Poisoned"],
                    "remindersGlobal": [],
                }
            }
        )
    )
    art = _write_png(
        tmp_path / "art.png", lambda d: d.ellipse([50, 50, 350, 350], fill="black")
    )
    custom = tmp_path / "custom_roles.json"
    custom.write_text(
        json.dumps(
            {
                "Beacon": {
                    "image": art.name,
                    "color": "blue",
                    "reminders": ["Watched"],
                },
                "Poisoner": {"image": art.name, "color": "purple"},
            }
        )
    )

    roles = sm.load_role_definitions(base, [custom])

    assert set(roles) == {"Poisoner", "Beacon"}  # added + overridden, not duplicated
    assert roles["Poisoner"]["color"] == "purple"  # custom wins
    assert Path(roles["Beacon"]["image"]).is_absolute()  # local path resolved
    assert roles["Beacon"]["remindersGlobal"] == []  # defaults filled in


def test_custom_role_image_may_be_omitted_for_text_only_tokens(tmp_path):
    import solid_maker as sm

    custom = tmp_path / "c.json"
    custom.write_text(json.dumps({"Night Order": {"color": "#FF8800"}}))

    roles = sm.load_role_definitions(None, [custom])

    assert roles["Night Order"]["image"] is None


def test_text_only_overlays_render_without_art(tmp_path):
    from solid import scad_render

    import solid_maker as sm

    character = scad_render(sm.role_overlay_model("Night Order", None))
    reminder = scad_render(sm.reminder_overlay_model("First night", None))

    assert '"NIGHT ORDER"' not in character  # name is drawn glyph by glyph
    assert "polygon" not in character  # no art
    assert '"FIRST"' in reminder or '"FIRST NIGHT"' in reminder
    assert "polygon" not in reminder


def test_custom_colour_may_be_an_explicit_hex():
    from solid_maker import overlay_colour_hex

    assert overlay_colour_hex("#FF8800") == "#FF8800FF"
    assert overlay_colour_hex("#ff8800aa") == "#FF8800AA"
    assert overlay_colour_hex("red") == "#8C0E12FF"
    assert overlay_colour_hex("chartreuse") == "#9AA0A6FF"  # unknown -> default


def test_invalid_custom_roles_are_reported_together(tmp_path):
    import solid_maker as sm

    custom = tmp_path / "bad.json"
    custom.write_text(
        json.dumps(
            {
                "Broken": {
                    "image": "missing.png",
                    "color": "chartreuse",
                    "reminders": "not-a-list",
                }
            }
        )
    )

    with pytest.raises(ValueError) as excinfo:
        sm.load_role_definitions(None, [custom])

    message = str(excinfo.value)
    assert "color" in message and "reminders" in message and "not found" in message


def test_check_art_quality_flags_blank_and_hairline_art(tmp_path):
    from PIL import Image

    import solid_maker as sm

    blank = tmp_path / "blank.png"
    Image.new("L", (400, 400), 255).save(blank)
    assert any("blank" in w for w in sm.check_art_quality(blank))

    solid = tmp_path / "solid.png"
    Image.new("L", (400, 400), 0).save(solid)
    assert any("solid" in w for w in sm.check_art_quality(solid))

    hairline = tmp_path / "hairline.png"
    _write_png(
        hairline,
        lambda d: [
            d.line([(x, 40), (x, 360)], fill="black", width=2)
            for x in range(0, 400, 26)
        ],
    )
    Image.open(hairline).convert("L").save(hairline)
    assert any("finer than" in w for w in sm.check_art_quality(hairline))


def test_check_art_quality_is_quiet_for_bold_art(tmp_path):
    from PIL import Image

    import solid_maker as sm

    bold = tmp_path / "bold.png"
    _write_png(bold, lambda d: d.ellipse([80, 80, 320, 320], fill="black"))
    Image.open(bold).convert("L").save(bold)

    assert sm.check_art_quality(bold) == []


# ---------------------------------------------------------------------------
# Scripts / editions
# ---------------------------------------------------------------------------
def test_load_scripts_skips_comment_keys(tmp_path):
    import solid_maker as sm

    path = tmp_path / "scripts.json"
    path.write_text(
        json.dumps({"_comment": ["docs"], "Trouble Brewing": ["Imp", "Poisoner"]})
    )

    scripts = sm.load_scripts(path)

    assert scripts == {"Trouble Brewing": ["Imp", "Poisoner"]}


def test_filter_roles_by_script_matches_names_loosely():
    import solid_maker as sm

    roles = {"Snake Charmer": {}, "Imp": {}, "Chef": {}}
    scripts = {"Sects": ["snakecharmer", "IMP"]}

    selected = sm.filter_roles_by_script(roles, "sects", scripts)

    assert set(selected) == {"Snake Charmer", "Imp"}  # case/spacing insensitive


def test_filter_roles_by_script_rejects_unknown_script():
    import solid_maker as sm

    with pytest.raises(KeyError, match="No script named"):
        sm.filter_roles_by_script({}, "Nope", {"Trouble Brewing": []})


def test_script_for_role_lists_every_containing_script():
    import solid_maker as sm

    scripts = {"A": ["Imp"], "B": ["imp", "Chef"], "C": ["Chef"]}

    assert sm.script_for_role("Imp", scripts) == "A, B"
    assert sm.script_for_role("Butler", scripts) == ""


# ---------------------------------------------------------------------------
# Friendly JSON errors for hand-edited config files
# ---------------------------------------------------------------------------
def test_load_json_file_reports_line_column_and_snippet(tmp_path):
    import solid_maker as sm

    path = tmp_path / "scripts.json"
    path.write_text('{\n  "A": ["Imp"]\n  "B": ["Chef"]\n}\n')  # missing comma

    with pytest.raises(ValueError) as excinfo:
        sm.load_json_file(path)

    message = str(excinfo.value)
    assert "not valid JSON" in message
    assert "line 3" in message
    assert '"B": ["Chef"]' in message  # shows the offending line
    assert "^" in message  # points at the column
    assert "missing comma" in message


def test_load_json_file_flags_curly_quotes(tmp_path):
    import solid_maker as sm

    path = tmp_path / "scripts.json"
    path.write_text('{\n  \u201cA\u201d: ["Imp"]\n}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="curly quotes"):
        sm.load_json_file(path)


def test_load_json_file_flags_trailing_comma(tmp_path):
    import solid_maker as sm

    path = tmp_path / "roles.json"
    path.write_text('{\n  "A": ["Imp"],\n}\n')

    with pytest.raises(ValueError, match="trailing comma"):
        sm.load_json_file(path)


def test_load_json_file_tolerates_a_utf8_bom(tmp_path):
    """Notepad and some editors prepend a BOM, which json.load chokes on."""
    import solid_maker as sm

    path = tmp_path / "scripts.json"
    path.write_text('{"A": ["Imp"]}', encoding="utf-8-sig")

    assert sm.load_json_file(path) == {"A": ["Imp"]}


def test_unknown_script_suggests_a_close_match():
    """A one-letter typo should be called out, not left for the eye to spot."""
    import solid_maker as sm

    scripts = {"Indigestion Brewing": [], "Trouble Brewing": []}

    with pytest.raises(KeyError) as excinfo:
        sm.filter_roles_by_script({}, "Indejestion Brewing", scripts)

    assert "Did you mean 'Indigestion Brewing'?" in str(excinfo.value)


def test_unknown_script_does_not_invent_a_suggestion():
    import solid_maker as sm

    with pytest.raises(KeyError) as excinfo:
        sm.filter_roles_by_script({}, "Bad Moon Rising", {"Trouble Brewing": []})

    assert "Did you mean" not in str(excinfo.value)


def test_script_name_matching_ignores_case_and_padding():
    import solid_maker as sm

    roles = {"Imp": {}}
    selected = sm.filter_roles_by_script(
        roles, "  trouble brewing ", {"Trouble Brewing": ["Imp"]}
    )

    assert set(selected) == {"Imp"}


def test_missing_script_roles_are_named_not_just_counted(capsys):
    """A count alone can't be acted on; the name points at the typo."""
    import solid_maker as sm

    roles = {"Imp": {}}
    sm.filter_roles_by_script(roles, "TB", {"TB": ["Imp", "Amnesiac", "Mezepheles"]})

    output = capsys.readouterr().out
    assert "Amnesiac" in output and "Mezepheles" in output


def test_pkg_resources_deprecation_warning_is_suppressed():
    """solidpython triggers it on every run and nothing here can act on it."""
    import warnings

    import solid_maker  # noqa: F401  (import registers the filter)

    assert any(
        action == "ignore"
        and message is not None
        and message.match("pkg_resources is deprecated")
        for action, message, category, _, _ in warnings.filters
    )
