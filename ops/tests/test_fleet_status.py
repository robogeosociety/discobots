"""Hermetic tests for fleet_status — the fleet-status board (one source → two surfaces).

`ops/fleet.toml` is the single source of truth. The load-bearing guard is
`test_committed_page_is_in_sync`: the committed `docs/fleet-status.md` MUST equal
what `render_markdown()` produces, so editing the inventory without regenerating
the page (`python3 ops/fleet_status.py --markdown docs/fleet-status.md`) fails CI
instead of silently drifting. The Discord panel is checked for the same groups.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import fleet_status as fs  # noqa: E402
from discokit import tokens  # noqa: E402


def _inv():
    return fs.load()


def test_inventory_loads_every_group():
    inv = _inv()
    for group in ("bots", "collectors", "data_sources", "graphs"):
        assert inv.get(group), f"fleet.toml has no {group}"
    assert inv["meta"]["wiki_url"].startswith("https://")


def test_committed_page_is_in_sync():
    # The committed page must be exactly what the renderer emits from fleet.toml.
    # If this fails: `python3 ops/fleet_status.py --markdown docs/fleet-status.md`.
    rendered = fs.render_markdown(_inv()).rstrip()
    committed = fs.DEFAULT_MD.read_text().rstrip()
    assert rendered == committed, (
        "docs/fleet-status.md is stale — regenerate it from fleet.toml"
    )


def test_discord_panel_carries_every_group_as_a_field():
    inv = _inv()
    embed = fs.render_discord(inv)["embeds"][0]
    assert embed["color"] == tokens.BLURPLE
    names = [f["name"] for f in embed["fields"]]
    assert any("ops bots" in n for n in names)
    assert any("collectors" in n for n in names)
    assert any("data sources" in n for n in names)
    assert any("graph kit" in n for n in names)
    # every field non-empty, and the counts match the inventory
    bots_field = next(f for f in embed["fields"] if "ops bots" in f["name"])
    assert f"({len(inv['bots'])})" in bots_field["name"]
    assert "obsidian-supervisor" in bots_field["value"]
    assert inv["meta"]["wiki_url"] in embed["description"]


def test_discord_panel_respects_embed_limits():
    embed = fs.render_discord(_inv())["embeds"][0]
    for f in embed["fields"]:
        assert 0 < len(f["value"]) <= 1024  # Discord's per-field cap
    assert (
        len(str(embed)) < 6000
    )  # Discord's per-embed cap (approx, str is an over-estimate)


def test_status_dot_falls_back_to_unknown():
    assert fs._dot("operational") == tokens.OPERATIONAL.dot
    assert fs._dot("nonsense") == tokens.UNKNOWN.dot
