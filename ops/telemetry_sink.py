#!/usr/bin/env python3
"""telemetry_sink — persist fleet telemetry from the bus into a local DuckDB.

The storage half of obsidian-automations#179's "SQLite/DuckDB companion": the
message bus (docs/BUS.md) is the *transport*, this is the *store*. A durable
stream consumer drains `fleet.telemetry` (at-least-once, per-consumer offset)
into one DuckDB file and prunes past a retention window — zero external
infrastructure, a portable `.db` you can copy/query with DuckDB's analytics.

Division of labour: real-time stays on the bus's retained last-value (the #ops
dashboards render it live, off the store's hot path — the exact TIG con #179
flags); this owns *history + analytics*. InfluxDB is no longer required in the
path just to move a metric from producer to screen.

It runs as its OWN supervised loop, deliberately (obsidian-automations#149's
fault-isolation): a telemetry writer that stalls or bloats must not take down
the loops it observes. A bus outage simply means nothing to drain. duckdb is
imported lazily so importing this module needs no dependency.

    # drain once against a bus + db, print the row count (smoke):
    python3 ops/telemetry_sink.py --once --db /tmp/telemetry.duckdb

    # run as the daemon (own container; BUS_URL + TELEMETRY_DB from env):
    python3 ops/telemetry_sink.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit.bus import Bus  # noqa: E402

STREAM = "fleet.telemetry"   # the durable telemetry stream (docs/BUS.md)
GROUP = "duckdb-sink"        # our consumer group — offset persists in the bus
DEFAULT_DB = "/state/telemetry.duckdb"
DEFAULT_RETENTION_DAYS = 30
PRUNE_EVERY_S = 3600         # prune at most hourly (retention is coarse)


class TelemetrySink:
    """One DuckDB file + the drain/prune operations over it."""

    def __init__(self, db_path: str | Path, *, retention_days: int = DEFAULT_RETENTION_DAYS) -> None:
        import duckdb

        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(self.db_path)
        self.retention_days = retention_days
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        # msg_id (the bus stream id) is the natural key → idempotent inserts, so
        # an at-least-once redelivery after a crash-before-ack can't double-count.
        self.con.execute(
            """
            CREATE TABLE IF NOT EXISTS telemetry (
                msg_id VARCHAR PRIMARY KEY,
                ts     DOUBLE,
                src    VARCHAR,
                type   VARCHAR,
                topic  VARCHAR,
                data   JSON
            )
            """
        )

    def drain(self, bus: Bus, *, batch: int = 256, block_ms: int = 0) -> int:
        """Read pending stream entries → insert → ack. Returns rows written."""
        msgs = bus.read_group(STREAM, GROUP, _consumer(), count=batch, block_ms=block_ms)
        if not msgs:
            return 0
        rows = [
            (
                mid,
                float(env.get("ts") or 0.0),
                env.get("src"),
                env.get("type"),
                env.get("topic"),
                json.dumps(env.get("data")),
            )
            for mid, env in msgs
        ]
        self.con.executemany(
            "INSERT INTO telemetry VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING", rows
        )
        bus.ack(STREAM, GROUP, *[mid for mid, _ in msgs])
        return len(rows)

    def prune(self, *, now: float | None = None) -> int:
        """Delete rows past the retention window. Returns rows removed."""
        cutoff = (now if now is not None else time.time()) - self.retention_days * 86400
        before = self.count()
        self.con.execute("DELETE FROM telemetry WHERE ts < ?", [cutoff])
        return before - self.count()

    def count(self) -> int:
        return self.con.execute("SELECT count(*) FROM telemetry").fetchone()[0]

    def close(self) -> None:
        self.con.close()


def _consumer() -> str:
    return os.environ.get("TELEMETRY_CONSUMER", "sink-1")


# --- daemon --------------------------------------------------------------------
def run_loop(sink: TelemetrySink, bus: Bus, *, poll_s: float) -> None:
    import signal

    stop = {"flag": False}

    def _halt(signum, _frame):
        print(f"\n[telemetry] {signal.Signals(signum).name} — shutting down", flush=True)
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _halt)
    signal.signal(signal.SIGINT, _halt)

    if not bus.enabled:
        print("[telemetry] no reachable bus (set BUS_URL) — nothing to drain", file=sys.stderr)

    last_prune = 0.0
    print(f"[telemetry] draining {STREAM} → {sink.db_path} (retention {sink.retention_days}d)")
    while not stop["flag"]:
        # A blocking read parks the loop until an entry lands (or the poll
        # window elapses), so an idle bus costs nothing.
        n = sink.drain(bus, block_ms=int(poll_s * 1000))
        if n:
            print(f"[telemetry] +{n} rows (total {sink.count()})")
        now = time.time()
        if now - last_prune > PRUNE_EVERY_S:
            removed = sink.prune(now=now)
            if removed:
                print(f"[telemetry] pruned {removed} rows past {sink.retention_days}d")
            last_prune = now
    sink.close()
    print("[telemetry] stopped.")


def main() -> None:
    ap = argparse.ArgumentParser(description="persist fleet telemetry from the bus into DuckDB")
    ap.add_argument("--db", default=os.environ.get("TELEMETRY_DB", DEFAULT_DB))
    ap.add_argument("--bus-url", default=os.environ.get("BUS_URL"))
    ap.add_argument("--retention-days", type=int,
                    default=int(os.environ.get("TELEMETRY_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)))
    ap.add_argument("--poll", type=float, default=5.0, help="blocking-read window seconds")
    ap.add_argument("--once", action="store_true", help="drain once (+prune) and exit")
    args = ap.parse_args()

    sink = TelemetrySink(args.db, retention_days=args.retention_days)
    bus = Bus(args.bus_url, src="telemetry-sink")

    if args.once:
        n = sink.drain(bus)
        removed = sink.prune()
        print(f"[telemetry] drained {n}, pruned {removed}, total {sink.count()} → {args.db}")
        sink.close()
        return

    run_loop(sink, bus, poll_s=args.poll)


if __name__ == "__main__":
    main()
