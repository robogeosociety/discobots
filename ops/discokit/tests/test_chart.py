# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "matplotlib"]
# ///
"""Unit tests for discokit.chart — the matplotlib PNG escape hatch.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest --with matplotlib python -m pytest discokit/tests/test_chart.py
"""

from __future__ import annotations

from discokit import chart

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_timeseries_returns_a_valid_png():
    data = chart.timeseries({"cpu": [1, 2, 3, 2, 1]})
    assert data.startswith(_PNG_MAGIC)
    assert len(data) > 500  # not a truncated/empty stream


def test_timeseries_empty_series_still_renders_a_placeholder():
    assert chart.timeseries({}).startswith(_PNG_MAGIC)
    assert chart.timeseries({"cpu": []}).startswith(_PNG_MAGIC)


def test_timeseries_with_timestamps_renders():
    data = chart.timeseries(
        {"cpu": [1, 2, 3]}, timestamps=[1_720_000_000, 1_720_000_060, 1_720_000_120]
    )
    assert data.startswith(_PNG_MAGIC)


def test_timeseries_multi_series_differs_from_single_series():
    one = chart.timeseries({"cpu": [1, 2, 3, 4, 5]})
    two = chart.timeseries({"cpu": [1, 2, 3, 4, 5], "mem": [5, 4, 3, 2, 1]})
    assert one != two  # the legend + second line change the raster


def test_timeseries_mismatched_lengths_do_not_crash():
    data = chart.timeseries({"short": [1, 2], "long": [1, 2, 3, 4, 5, 6]})
    assert data.startswith(_PNG_MAGIC)
