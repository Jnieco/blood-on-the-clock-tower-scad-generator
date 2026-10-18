"""Tests for arranging tokens onto build plates."""

import argparse
import zipfile

import pytest

import make_plates as mp
import solid_maker as sm


# -- grid maths (no geometry needed) ---------------------------------------
def test_grid_fills_the_bed_and_reports_shape():
    positions, per_plate, cols, rows = mp._grid_positions(
        count=4, cell=50.0, bed=(256, 256), margin=5.0
    )

    assert cols == 4 and rows == 4  # (256-10)//50 = 4
    assert per_plate == 16
    assert len(positions) == 4


def test_grid_overflows_onto_more_plates():
    positions, per_plate, cols, rows = mp._grid_positions(
        count=20, cell=50.0, bed=(256, 256), margin=5.0
    )

    plates = {p for p, _, _ in positions}
    assert per_plate == 16
    assert plates == {0, 1}  # 20 tokens -> 16 + 4


def test_grid_positions_stay_within_the_bed():
    cell = 50.0
    positions, _, _, _ = mp._grid_positions(16, cell, (256, 256), 5.0)

    half = 256 / 2
    for _, x, y in positions:
        assert abs(x) + cell / 2 <= half
        assert abs(y) + cell / 2 <= half


def test_a_full_plate_is_centred_on_the_origin():
    positions, _, _, _ = mp._grid_positions(16, 50.0, (256, 256), 5.0)

    xs = [x for _, x, _ in positions]
    ys = [y for _, y, _ in positions]
    assert sum(xs) == pytest.approx(0.0)
    assert sum(ys) == pytest.approx(0.0)


def test_a_partial_plate_is_also_centred():
    # One short row must not push everything into the top of the bed.
    positions, _, _, _ = mp._grid_positions(3, 50.0, (256, 256), 5.0)

    ys = [y for _, y, _ in positions]
    assert sum(ys) / len(ys) == pytest.approx(0.0)


def test_tiny_bed_still_yields_one_cell():
    positions, per_plate, cols, rows = mp._grid_positions(3, 50.0, (40, 40), 5.0)

    assert (cols, rows, per_plate) == (1, 1, 1)
    assert {p for p, _, _ in positions} == {0, 1, 2}  # one per plate


def test_bed_parser_reads_dimensions():
    assert sm.parse_bed_size("256x256") == (256, 256)
    assert sm.parse_bed_size("180X200") == (180, 200)


def test_bed_parser_rejects_bad_input():
    with pytest.raises(argparse.ArgumentTypeError):
        sm.parse_bed_size("wide")


# -- collecting tokens ------------------------------------------------------
def test_collect_tokens_only_includes_existing_overlays(tmp_path, monkeypatch):
    monkeypatch.setattr(mp.sm, "STL_DIR", tmp_path)
    # Only the Imp coin overlay exists on disk.
    (tmp_path / "red_Imp_coin_overlay.stl").write_text("x")

    roles = {
        "Imp": {"color": "red", "reminders": ["Dead", "Dead"], "remindersGlobal": []},
        "Chef": {"color": "blue", "reminders": [], "remindersGlobal": []},
    }
    tokens = mp.collect_tokens(roles)

    labels = [label for *_, label in tokens]
    assert labels == ["Imp"]  # Chef overlay missing; Dead reminder overlay missing


def test_collect_tokens_dedupes_reminders(tmp_path, monkeypatch):
    monkeypatch.setattr(mp.sm, "STL_DIR", tmp_path)
    (tmp_path / "red_Imp_coin_overlay.stl").write_text("x")
    (tmp_path / "red_Imp_Dead_reminder.stl").write_text("x")

    roles = {
        "Imp": {
            "color": "red",
            "reminders": ["Dead", "Dead", "Dead"],
            "remindersGlobal": [],
        }
    }
    tokens = mp.collect_tokens(roles)

    dead = [label for *_, label in tokens if "Dead" in label]
    assert len(dead) == 1  # three copies -> one plate slot


# -- end to end (real geometry) --------------------------------------------
@pytest.fixture
def generated(tmp_path, monkeypatch):
    """Generate a couple of tokens in two colours into a temp workspace."""
    from PIL import Image, ImageDraw

    for name in (
        "PNG_DIR",
        "GREY_PNG_DIR",
        "SVG_DIR",
        "SCAD_DIR",
        "STL_DIR",
        "THREEMF_DIR",
    ):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(sm, name, d)
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    art = art_dir / "a.png"
    im = Image.new("RGBA", (400, 400), (255, 255, 255, 0))
    ImageDraw.Draw(im).ellipse([90, 90, 310, 310], fill=(10, 10, 10, 255))
    im.save(art)

    roles = {
        "Imp": {
            "image": str(art),
            "color": "red",
            "reminders": ["Dead"],
            "remindersGlobal": [],
        },
        "Chef": {
            "image": str(art),
            "color": "blue",
            "reminders": [],
            "remindersGlobal": [],
        },
    }
    # base discs + overlays
    for label, model in (
        ("000_coin_base_2mm_felt", sm.felt_coin_model()),
        ("000_reminder_base_2mm_felt", sm.reminder_base_model()),
    ):
        scad = sm.SCAD_DIR / f"{label}.scad"
        sm.write_scad(model, scad)
        sm.export_coin_to_stl(model, scad, sm.STL_DIR / f"{label}.stl")
    for role, data in roles.items():
        sm.build_character_token(role, data, make_3mf=False)
        svg = sm.SVG_DIR / f"{sm.safe_name(role)}.svg"
        sm.build_reminder_tokens(
            role, data, svg if svg.exists() else None, make_3mf=False
        )
    return roles


def test_build_plates_groups_by_colour(generated, tmp_path):
    out = tmp_path / "plates"

    reports = mp.build_plates(generated, bed=(256, 256), out_dir=out)

    colours = {r["colour"] for r in reports}
    assert colours == {"red", "blue"}
    # Each plate file holds only its own colour plus the dark base.
    for report in reports:
        xml = zipfile.ZipFile(report["file"]).read("3D/3dmodel.model").decode()
        assert xml.count("<m:color ") == 2  # exactly two, plus one <m:colorgroup>
        assert "#2E2E2EFF" in xml


def test_plate_places_every_body_with_a_transform(generated, tmp_path):
    reports = mp.build_plates(generated, out_dir=tmp_path / "plates")

    red = next(r for r in reports if r["colour"] == "red")
    xml = zipfile.ZipFile(red["file"]).read("3D/3dmodel.model").decode()
    # Imp coin + Imp Dead reminder = 2 tokens = 4 bodies = 4 placed items.
    assert xml.count("<item ") == 4
    assert xml.count("transform=") == 4


def test_build_plates_needs_generated_tokens(tmp_path, monkeypatch):
    monkeypatch.setattr(mp.sm, "STL_DIR", tmp_path)  # empty

    with pytest.raises(ValueError, match="No token STLs"):
        mp.build_plates({"Imp": {"color": "red"}}, out_dir=tmp_path / "p")
