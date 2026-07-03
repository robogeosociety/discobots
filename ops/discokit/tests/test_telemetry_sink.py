# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "fakeredis", "duckdb"]
# ///
"""Unit tests for telemetry_sink — the DuckDB store half of #179.

Run (from ops/, so telemetry_sink + discokit resolve):
    cd ops && uv run --with pytest --with fakeredis --with duckdb \
        python -m pytest discokit/tests/test_telemetry_sink.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # ops/

pytest.importorskip("duckdb")
from discokit.bus import Bus  # noqa: E402
from telemetry_sink import STREAM, TelemetrySink  # noqa: E402


@pytest.fixture
def bus():
    fakeredis = pytest.importorskip("fakeredis")
    return Bus(src="test", client=fakeredis.FakeRedis(decode_responses=True))


@pytest.fixture
def sink(tmp_path):
    s = TelemetrySink(tmp_path / "t.duckdb", retention_days=30)
    yield s
    s.close()


def test_drain_persists_emitted_telemetry(bus, sink):
    bus.emit(STREAM, {"metric": "supervisor.tick", "lag_s": 0.2})
    bus.emit(STREAM, {"metric": "supervisor.tick", "lag_s": 0.4})
    assert sink.drain(bus) == 2
    assert sink.count() == 2
    row = sink.con.execute(
        "SELECT src, data->>'$.metric', CAST(data->>'$.lag_s' AS DOUBLE) "
        "FROM telemetry ORDER BY data->>'$.lag_s'"
    ).fetchall()
    assert row[0][0] == "test" and row[0][1] == "supervisor.tick"
    assert row[1][2] == 0.4  # JSON round-trips and is queryable


def test_drain_is_idempotent_on_redelivery(bus, sink):
    mid = bus.emit(STREAM, {"metric": "x"})
    assert sink.drain(bus) == 1
    # simulate an at-least-once redelivery of the SAME stream id (crash before ack)
    sink.con.executemany(
        "INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [(mid, time.time(), "test", "event", STREAM, '{"metric": "x"}')],
    )
    assert sink.count() == 1  # PRIMARY KEY on msg_id ⇒ no double-count


def test_prune_drops_rows_past_retention(bus, sink):
    old = time.time() - 40 * 86400
    sink.con.executemany(
        "INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [("old-1", old, "s", "event", STREAM, "{}"), ("new-1", time.time(), "s", "event", STREAM, "{}")],
    )
    assert sink.count() == 2
    assert sink.prune() == 1
    assert sink.count() == 1
    assert sink.con.execute("SELECT msg_id FROM telemetry").fetchone()[0] == "new-1"


def test_drain_empty_bus_is_zero(bus, sink):
    assert sink.drain(bus) == 0


def test_disabled_bus_drains_nothing(sink):
    assert sink.drain(Bus(url=None)) == 0  # no bus ⇒ read_group returns [] ⇒ 0 rows
