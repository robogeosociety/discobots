# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.graph — the btop-style text-graph primitives.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_graph.py
"""

from __future__ import annotations

from discokit import graph


def test_resample_keeps_endpoints_and_length():
    out = graph.resample([0, 10], 5)
    assert len(out) == 5
    assert out[0] == 0 and out[-1] == 10
    assert graph.resample([7], 4) == [7.0] * 4
    assert graph.resample([], 4) == []


def test_spark_flat_series_renders_full_height():
    assert graph.spark([5, 5, 5]) == "███"
    assert graph.spark([]) == "—"


def test_spark_respects_width():
    s = graph.spark(list(range(100)), width=24)
    assert len(s) == 24
    assert s[0] == "▁" and s[-1] == "█"


def test_bar_full_empty_and_zero_total():
    assert graph.bar(10, 10, width=8) == "████████"
    assert graph.bar(0, 10, width=8) == "░░░░░░░░"
    assert graph.bar(3, 0, width=8) == "░░░░░░░░"  # empty track, not a crash
    assert len(graph.bar(3, 10, width=8)) == 8


def test_braille_shape_and_charset():
    out = graph.braille(list(range(50)), width=20, height=4)
    lines = out.splitlines()
    assert len(lines) == 4
    assert all(len(line) == 20 for line in lines)
    assert all(0x2800 <= ord(ch) <= 0x28FF for line in lines for ch in line)


def test_braille_area_rises_with_the_series():
    lines = graph.braille(list(range(50)), width=20, height=4).splitlines()
    bottom, top = lines[-1], lines[0]
    assert bottom[-1] == "⣿"  # max value: bottom-right cell fully lit
    assert top[0] == "⠀" and top[-1] != "⠀"  # top row lights up only near the max
    assert bottom[0] != "⠀"  # baseline always draws at least one dot


def test_braille_flat_series_draws_a_plateau():
    lines = graph.braille([3, 3, 3], width=10, height=4).splitlines()
    assert lines[-1].count("⣿") == 10  # bottom half filled…
    assert lines[0] == "⠀" * 10  # …top empty


def test_chips_group_per_line():
    items = [(f"s{i}", "🟢") for i in range(6)]
    out = graph.chips(items, per_line=4)
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].count("🟢") == 4 and lines[1].count("🟢") == 2
    assert "🟢 s0" in lines[0]
