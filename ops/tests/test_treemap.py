"""Tests for discokit.treemap — squarify + grid/legend + human_mb."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from discokit import treemap  # noqa: E402


def test_human_mb_units():
    assert treemap.human_mb(512) == "512M"
    assert treemap.human_mb(1024) == "1.0G"
    assert treemap.human_mb(1536) == "1.5G"


def test_grid_is_h_by_w_and_fully_filled():
    body, legend = treemap.grid_and_legend([("a", 10.0), ("b", 6.0), ("c", 3.0)])
    rows = body.splitlines()
    assert len(rows) == treemap.H
    assert all(len(r) == treemap.W for r in rows)
    # every rendered square is one of the palette emoji (no None holes)
    assert set("".join(rows)) <= set(treemap.EMOJI)
    assert len(legend.splitlines()) == 3


def test_single_item_fills_the_whole_grid():
    body, _ = treemap.grid_and_legend([("solo", 100.0)])
    assert set(body.replace("\n", "")) == {treemap.EMOJI[0]}


def test_squarify_drops_nonpositive_values():
    rects = treemap.squarify([("a", 5.0), ("z", 0.0), ("n", -1.0)], 0, 0, 12, 8)
    assert {r[0] for r in rects} == {"a"}
