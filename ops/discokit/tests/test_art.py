# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.art — the MI1-discipline scene primitives.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_art.py
"""

from __future__ import annotations

from discokit import art


def test_shade_clamps_and_uses_the_ramp_ends():
    assert art.shade(0.0) == " "
    assert art.shade(1.0) == "@"
    assert art.shade(-5) == " " and art.shade(5) == "@"


def test_shade_dithers_midtones_with_neighbouring_glyphs():
    ramp = " .:"
    # 0.25 sits halfway between ramp steps ' ' and '.' — the Bayer matrix
    # must alternate the two (an exact step like 0.5 correctly renders solid).
    cells = {art.shade(0.25, x, y, ramp=ramp) for x in range(4) for y in range(4)}
    assert cells == {" ", "."}
    assert {art.shade(0.5, x, y, ramp=ramp) for x in range(4) for y in range(4)} == {"."}


def test_band_fills_rows_and_respects_bounds():
    c = art.Canvas(8, 4)
    c.band(1, 3, 1.0, ramp=" #")
    rows = c.render().splitlines()
    assert rows[1] == "########" and rows[2] == "########"
    assert len(rows[0].strip()) == 0


def test_sprite_overlays_with_transparent_spaces():
    c = art.Canvas(6, 2)
    c.band(0, 2, 1.0, ramp=" .")
    c.sprite(1, 0, "@ @")
    assert c.render().splitlines()[0] == ".@.@.."


def test_scatter_is_deterministic_and_sparse():
    a, b = art.Canvas(40, 5), art.Canvas(40, 5)
    a.scatter(0, 5, 0.05)
    b.scatter(0, 5, 0.05)
    assert a.render() == b.render()
    lit = sum(row.count(".") for row in a.render().splitlines())
    assert 0 < lit < 40  # sparse, not a lattice fill


def test_glow_brightens_toward_the_light_source():
    c = art.Canvas(20, 5)
    c.band(0, 5, 0.1)
    c.glow(10, 2, 8.0, boost=0.9)
    ramp = art.RAMP
    centre = ramp.find(c.grid[2][10])
    edge = ramp.find(c.grid[2][0])
    assert centre > edge


def test_melee_dock_fits_the_grid_contract():
    scene = art.melee_dock()
    lines = scene.splitlines()
    assert len(lines) == 18
    assert max(len(line) for line in lines) <= 64
    assert "@" in scene  # the one light source exists


# ── the scene register + mechanical grammar (Phase 1, Monkey Island UI) ──────


def test_scene_registry_renders_guide_compliant():
    for name, fn in art.SCENES.items():
        scene = fn()
        assert scene.strip(), name
        assert art.check(scene) == [], (name, art.check(scene))
        assert art.CAPTIONS[name].strip(), name


def test_captions_stay_out_of_the_scene_register():
    # captions are prose (diacritics allowed); scenes stay pure ASCII
    for name, fn in art.SCENES.items():
        assert all(ord(ch) < 128 for ch in fn()), name


def test_dock_dawn_reads_dawn():
    scene = art.dock_dawn()
    assert "(@@@@)" in scene  # the risen sun
    assert "@" not in "\n".join(scene.splitlines()[:3])  # no moon in the high sky
    assert "v" in scene  # the gulls


def test_ship_underway_reads_from_outline():
    scene = art.ship_underway()
    assert "|##\\" in scene  # the rigged mainmast survives the light pass
    assert "\\############/" in scene  # the hull mass, freeboard and all
    assert "@" in scene  # the stern lantern


def test_check_flags_each_banned_register():
    assert art.check("▓▒░") != []  # ANSI/DOS blocks — graph.py's genre
    assert art.check("x" * 81) != []  # wider than the grid contract
    assert art.check("\n".join("." for _ in range(26))) != []  # taller
    assert art.check("🔥🌙⭐") != []  # three accents past the budget
    assert art.check("🔥🌙") == []  # two accents IS the budget
    assert art.check(art.RAMP.strip()) != []  # the full 9-glyph ramp — no subset restraint
