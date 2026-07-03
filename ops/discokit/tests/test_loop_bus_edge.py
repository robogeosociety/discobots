# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "fakeredis"]
# ///
"""The bus edge: loop_dashboard.fetch_live prefers the bus, falls back to Influx.

Run (from ops/, so `loop_dashboard` + `discokit` resolve):
    cd ops && uv run --with pytest --with fakeredis python -m pytest discokit/tests/test_loop_bus_edge.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ops/

import loop_dashboard as ld  # noqa: E402
from discokit.bus import Bus  # noqa: E402


@pytest.fixture
def bus():
    fakeredis = pytest.importorskip("fakeredis")
    return Bus(src="test", client=fakeredis.FakeRedis(decode_responses=True))


def test_fetch_live_prefers_the_bus(monkeypatch, bus):
    # Influx would raise if touched — proves the bus short-circuits it.
    monkeypatch.setattr(ld, "_fetch_influx", lambda cfg: (_ for _ in ()).throw(AssertionError("hit influx")))
    bus.publish(ld.BUS_TOPIC, {"ok": True, "lag_s": 0.3, "budget_free_pct": 90})
    snap = ld.fetch_live({}, bus)
    assert snap["ok"] is True and snap["budget_free_pct"] == 90


def test_fetch_live_falls_back_when_bus_quiet(monkeypatch, bus):
    monkeypatch.setattr(ld, "_fetch_influx", lambda cfg: {"ok": True, "source": "influx"})
    snap = ld.fetch_live({}, bus)  # nothing published ⇒ retained is None
    assert snap == {"ok": True, "source": "influx"}


def test_fetch_live_no_bus_uses_influx(monkeypatch):
    monkeypatch.setattr(ld, "_fetch_influx", lambda cfg: {"ok": True, "source": "influx"})
    assert ld.fetch_live({}, None)["source"] == "influx"
