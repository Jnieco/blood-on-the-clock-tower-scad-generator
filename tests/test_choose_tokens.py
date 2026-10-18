"""Tests for the full-size "Choose a ..." draft tokens."""

import numpy as np

import solid_maker as sm


def test_four_tokens_with_standard_team_colours():
    defs = sm.choose_token_definitions()

    assert set(defs) == {
        "Choose a Townsfolk",
        "Choose an Outsider",
        "Choose a Minion",
        "Choose a Demon",
    }
    # Good team blue, evil team red, matching the real tokens.
    assert defs["Choose a Townsfolk"]["color"] == "blue"
    assert defs["Choose an Outsider"]["color"] == "blue"
    assert defs["Choose a Minion"]["color"] == "red"
    assert defs["Choose a Demon"]["color"] == "red"


def test_tokens_are_text_only():
    for data in sm.choose_token_definitions().values():
        assert data["image"] is None
        assert data["reminders"] == []


def test_overlay_model_builds_scad_with_the_phrase():
    from solid import scad_render

    rendered = scad_render(sm.choose_a_overlay_model("Choose a Demon"))

    assert "text(" in rendered
    # The phrase is upper-cased and may wrap, so check a word survives.
    assert "DEMON" in rendered


def test_text_stays_inside_the_disc(tmp_path, monkeypatch):
    """Every draft token's inlay must clear the rim, or the pocket boolean
    leaves a non-manifold edge."""
    for name in ("SCAD_DIR", "STL_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(sm, name, d)

    # Build the base disc the 3MF needs, then each token, checking the overlay
    # STL radius stays within the disc.
    for label, _colour in sm.CHOOSE_TOKENS:
        model = sm.choose_a_overlay_model(label)
        scad = sm.SCAD_DIR / f"{sm.safe_name(label)}.scad"
        sm.write_scad(model, scad)
        stl = sm.STL_DIR / f"{sm.safe_name(label)}.stl"
        sm.export_coin_to_stl(model, scad, stl)
        import trimesh

        mesh = trimesh.load(stl)
        max_r = float(np.max(np.hypot(mesh.vertices[:, 0], mesh.vertices[:, 1])))
        # Inlay clipped to COIN_DIAMETER - CHOOSE_RIM_CLEARANCE.
        assert max_r <= (sm.COIN_DIAMETER - sm.CHOOSE_RIM_CLEARANCE) / 2 + 0.01


def test_overlay_is_a_clean_manifold_for_every_token(tmp_path, monkeypatch):
    """The short wide words (DEMON, MINION) used to leave a non-manifold edge
    where a glyph met the rim; all four must now be clean."""
    import trimesh

    for name in ("SCAD_DIR", "STL_DIR", "THREEMF_DIR"):
        d = tmp_path / name.lower()
        d.mkdir()
        monkeypatch.setattr(sm, name, d)

    # Base disc for the 3MF.
    base_model = sm.felt_coin_model()
    base_scad = sm.SCAD_DIR / "base.scad"
    sm.write_scad(base_model, base_scad)
    sm.export_coin_to_stl(
        base_model, base_scad, sm.STL_DIR / "000_coin_base_2mm_felt.stl"
    )

    for label, _colour in sm.CHOOSE_TOKENS:
        sm.build_choose_token(label, sm.choose_token_definitions()[label])

    for mesh_file in sm.THREEMF_DIR.glob("*.3mf"):
        scene = trimesh.load(mesh_file)
        for geometry in scene.geometry.values():
            _unique, counts = np.unique(
                geometry.edges_sorted, axis=0, return_counts=True
            )
            non_manifold = int((counts > 2).sum())
            open_edges = int((counts == 1).sum())
            assert non_manifold == 0, f"{mesh_file.name} has a non-manifold edge"
            assert open_edges == 0, f"{mesh_file.name} has an open edge"
