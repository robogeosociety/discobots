"""Hermetic tests for claude_heatmap.parse_matrix + render (no network)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import claude_heatmap as ch  # noqa: E402

CSV = (
    "#group,false,false,true,false\n"
    "#datatype,string,long,string,dateTime:RFC3339,double\n"
    ",result,table,project,_time,_value\n"
    ",_result,0,discobots,2026-07-21T00:00:00Z,1000\n"
    ",_result,0,discobots,2026-07-21T01:00:00Z,5000\n"
    ",_result,1,obsidian,2026-07-21T00:00:00Z,200\n"
    ",_result,1,obsidian,2026-07-21T01:00:00Z,0\n"
)


def test_parse_matrix_groups_by_project_and_sorts_times():
    matrix, times = ch.parse_matrix(CSV)
    assert set(matrix) == {"discobots", "obsidian"}
    assert times == ["2026-07-21T00:00:00Z", "2026-07-21T01:00:00Z"]
    assert matrix["discobots"]["2026-07-21T01:00:00Z"] == 5000.0


def test_render_orders_projects_by_total_desc_with_heat_cells():
    matrix, times = ch.parse_matrix(CSV)
    out = ch.render(matrix, times)
    assert out.startswith("🔥 **Claude tokens — heatmap**")
    body = out.split("\n\n")[1].splitlines()
    # discobots (6000) sorts above obsidian (200)
    assert "discobots" in body[0] and "obsidian" in body[1]
    # the hottest cell in the window uses the top of the ramp
    assert ch.HEAT[5] in out
    # an idle (zero) bucket renders as the idle glyph
    assert ch.HEAT[0] in out


def test_render_empty_window_is_graceful():
    out = ch.render({}, [])
    assert "(no token activity in window)" in out
