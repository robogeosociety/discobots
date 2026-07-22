"""Hermetic tests for orbmem.parse_rows + render (no network)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import orbmem  # noqa: E402

# Annotated InfluxDB CSV, bytes in _value; two containers, unsorted.
CSV = (
    "#group,false,false,true,false\n"
    "#datatype,string,long,string,double\n"
    ",result,table,container_name,_value\n"
    ",_result,0,discobot-live,104857600\n"      # 100 MiB
    ",_result,0,discobot-valkey,209715200\n"    # 200 MiB
)


def test_parse_rows_converts_bytes_to_mib_and_sorts_desc():
    rows = orbmem.parse_rows(CSV)
    assert rows[0][0] == "discobot-valkey" and abs(rows[0][1] - 200.0) < 0.01
    assert rows[1][0] == "discobot-live" and abs(rows[1][1] - 100.0) < 0.01


def test_parse_rows_skips_blank_and_malformed():
    assert orbmem.parse_rows("") == []
    assert orbmem.parse_rows("#only,comments\n") == []


def test_render_header_grid_and_legend():
    rows = orbmem.parse_rows(CSV)
    out = orbmem.render(rows)
    assert out.startswith("🐳 **OrbStack containers — memory**")
    assert "2 containers · 300M used" in out
    grid = out.split("\n\n")[1].splitlines()
    assert len(grid) == 8
    assert all(len(row) == 12 for row in grid)
    legend = out.split("\n\n")[2].splitlines()
    assert legend[0].endswith("discobot-valkey · 200M")


def test_render_caps_at_seven_tiles():
    rows = [(f"c{i}", float(100 - i)) for i in range(20)]
    legend = orbmem.render(rows).split("\n\n")[2].splitlines()
    assert len(legend) == 7
