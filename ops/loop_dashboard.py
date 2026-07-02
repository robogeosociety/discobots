#!/usr/bin/env python3
"""loop_dashboard — the supervisor loop as a spinning ASCII ferris wheel, in #ops.

ONE self-editing Discord message that draws `obsidiand` (the asyncio + pydoit loop) as a ferris
wheel from its `supervisor_tick` telemetry in the InfluxDB `ops` bucket. The wheel turns one cabin
per minute (so a live message visibly spins as the loop ticks); the **lead** cabin ◉ is the last
tick and a ✦ cabin marks the last event — both labeled with a relative timestamp. A compact footer
carries the trigger split (cron / event / backstop) + fire count, and the header the lag / shadow
health. Posts to #ops (the general/ops webhook), beside the #ops status dashboard. Mirrors
ops_dashboard.py — posts once, PATCH-edits in place, only bumps on real change (discokit.Dashboard).

    # see the whole feel locally — no Influx, no Discord, no deps:
    python3 ops/loop_dashboard.py --dry --demo

    # run live on the mini against the real #ops webhook:
    python3 ops/loop_dashboard.py --interval 60 --iterations 0 --state /state/loop.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# discokit (the package) sits next to this file, in ops/ — and flat in /app inside the
# container. Put that dir on the path either way (same shim as ops_dashboard.py).
_OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(_OPS))

from discokit import config, tokens  # noqa: E402
from discokit.dashboard import Dashboard  # noqa: E402
from discokit.live import Job  # noqa: E402
from discokit.poster import Poster  # noqa: E402

BUCKET = "ops"
MEASUREMENT = "supervisor_tick"
WINDOW = "-2h"  # trigger/fire counts window
EVENT_WINDOW = "-24h"  # how far back to look for the last event (they can be sparse)
SPIN_SECS = 60  # the wheel advances one cabin per minute (the loop's ~60s beat)


# --- the ferris wheel ----------------------------------------------------------------------
# 8 cabins at clock positions 0(top)..7 clockwise. Single-char slots keep the monospace
# geometry when the glyphs (all width-1) are substituted in.
_FRAME = [
    "        0        ",
    "     7     1     ",
    "      ╲ │ ╱      ",
    "  6─────⊙─────2  ",
    "      ╱ │ ╲      ",
    "     5     3     ",
    "        4        ",
]


def ferris(lead: int | None, event: int | None) -> str:
    """Render the wheel: ``lead`` cabin as ◉ (freshest tick), ``event`` cabin as ✦, rest ●.

    ``lead`` None (a stopped/silent loop) leaves no highlighted cabin. ``lead`` wins on overlap."""
    g = {i: "●" for i in range(8)}
    if event is not None:
        g[event % 8] = "✦"
    if lead is not None:
        g[lead % 8] = "◉"
    return "\n".join("".join(g[int(c)] if c.isdigit() else c for c in row) for row in _FRAME)


def _slot(epoch: float | None) -> int | None:
    """The cabin a timestamp maps to — the wheel position that was 'at the top' at that minute."""
    return int(epoch // SPIN_SECS) % 8 if epoch else None


# --- rendering -----------------------------------------------------------------------------
def _status(snap: dict | None) -> tokens.Status:
    """Health of the loop: silent → critical, laggy → degraded, else ok."""
    if snap is None or not snap.get("ok"):
        return tokens.CRITICAL  # no supervisor_tick in the window → the wheel has stopped
    if snap.get("lag_s", 0) >= 5.0:  # a wedged loop lags; 5 s is generous vs the 60 s beat
        return tokens.DEGRADED
    return tokens.INFO  # healthy monitor (informational, not an alert)


def build_panel(snap: dict | None, last_good: dict | None) -> dict:
    """Build the dashboard embed body ({"embeds":[…]}) — no freshness stamp (Dashboard owns it)."""
    st = _status(snap)
    title = "🎡 obsidiand · the loop"
    if snap is None or not snap.get("ok"):
        body = f"{st.glyph} **the wheel has stopped** — no `supervisor_tick` in {_w(WINDOW)}"
        if last_good:
            body += "\n" + _wheel_block(last_good, spinning=False)
        return {"embeds": [{"title": title, "description": body, "color": st.color}]}

    mode = "shadow" if snap.get("shadow") else "live"
    header = f"{st.glyph} **{mode}** · lag {snap['lag_s']:.1f}s · mem {snap['budget_free_pct']}% free"
    return {
        "embeds": [{"title": title, "description": f"{header}\n{_wheel_block(snap)}", "color": st.color}]
    }


def _wheel_block(snap: dict, *, spinning: bool = True) -> str:
    """The ferris-wheel code block + the last-tick / last-event labels + a trigger footer."""
    lt, le = snap.get("last_tick_epoch"), snap.get("last_event_epoch")
    lead = _slot(lt) if spinning else None
    wheel = ferris(lead, _slot(le))
    cron, event, backstop = snap["by_cron"], snap["by_event"], snap["by_backstop"]
    fires, ran, fresh = snap.get("fires", 0), snap["doit_executed"], snap["doit_uptodate"]
    tick_lbl = f"◉ **last tick** <t:{int(lt)}:R>" if lt else "◉ last tick — never"
    evt_lbl = (
        f"✦ **last event** <t:{int(le)}:R> · {event} events/{_w(WINDOW)}"
        if le
        else f"✦ last event — none in {_w(EVENT_WINDOW)}"
    )
    footer = (
        f"cron {cron} · event {event} · backstop {backstop} · "
        f"{fires} fires/{_w(WINDOW)} · doit ran {ran}/{ran + fresh}"
    )
    return "\n".join(["```text", wheel, "```", tick_lbl, evt_lbl, footer])


def _w(window: str) -> str:
    return window.lstrip("-")  # "-2h" → "2h" for display


# --- live source: InfluxDB supervisor_tick -------------------------------------------------
def load_config() -> dict:
    """Influx creds from env (run.sh injects them from ask-dash/.env), matching digest.py."""
    ask = config.read_dotenv("~/dev/observability/ask-dash/.env")
    return {
        "url": os.environ.get("INFLUXDB_URL", ask.get("INFLUX_URL", "http://localhost:8086")),
        "token": os.environ.get("INFLUXDB_TOKEN", ask.get("INFLUX_READ_TOKEN", "")),
        "org": os.environ.get("INFLUXDB_ORG", ask.get("INFLUX_ORG", "home")),
    }


def fetch_live(cfg: dict) -> dict | None:
    """Query supervisor_tick → a snapshot dict, or None if Influx is unreachable / has no data."""
    try:
        from influxdb_client import InfluxDBClient
    except Exception:  # dependency missing (shouldn't happen in the container) — degrade
        return None
    try:
        client = InfluxDBClient(url=cfg["url"], token=cfg["token"], org=cfg["org"])
        api = client.query_api()
        # 1 — window totals per trigger/doit/fired field
        totals: dict[str, float] = {}
        for t in api.query(
            f'from(bucket:"{BUCKET}") |> range(start:{WINDOW})'
            f' |> filter(fn:(r)=> r._measurement=="{MEASUREMENT}")'
            f' |> filter(fn:(r)=> r._field=="by_cron" or r._field=="by_event" or'
            f' r._field=="by_backstop" or r._field=="doit_executed" or r._field=="doit_uptodate"'
            f' or r._field=="fired")'
            f' |> group(columns:["_field"]) |> sum()',
            org=cfg["org"],
        ):
            for r in t.records:
                totals[r.values.get("_field", "")] = r.get_value() or 0
        # 2 — latest health + the last-tick time + the shadow tag
        latest: dict[str, float] = {}
        shadow = False
        last_tick_epoch: float | None = None
        for t in api.query(
            f'from(bucket:"{BUCKET}") |> range(start:-15m)'
            f' |> filter(fn:(r)=> r._measurement=="{MEASUREMENT}")'
            f' |> filter(fn:(r)=> r._field=="lag_s" or r._field=="budget_free_pct")'
            f' |> last()',
            org=cfg["org"],
        ):
            for r in t.records:
                latest[r.values.get("_field", "")] = r.get_value() or 0
                shadow = shadow or str(r.values.get("shadow", "0")).startswith("1")
                if r.get_time():
                    last_tick_epoch = r.get_time().timestamp()
        # 3 — the last EVENT: most recent tick whose by_event counter was non-zero
        last_event_epoch: float | None = None
        for t in api.query(
            f'from(bucket:"{BUCKET}") |> range(start:{EVENT_WINDOW})'
            f' |> filter(fn:(r)=> r._measurement=="{MEASUREMENT}" and r._field=="by_event")'
            f' |> filter(fn:(r)=> r._value > 0) |> last()',
            org=cfg["org"],
        ):
            for r in t.records:
                if r.get_time():
                    last_event_epoch = r.get_time().timestamp()
        client.close()
        if not totals and not latest:
            return None  # nothing in the window → the wheel has stopped
        return {
            "ok": True,
            "shadow": shadow,
            "lag_s": float(latest.get("lag_s", 0.0)),
            "budget_free_pct": int(latest.get("budget_free_pct", 0)),
            "by_cron": int(totals.get("by_cron", 0)),
            "by_event": int(totals.get("by_event", 0)),
            "by_backstop": int(totals.get("by_backstop", 0)),
            "doit_executed": int(totals.get("doit_executed", 0)),
            "doit_uptodate": int(totals.get("doit_uptodate", 0)),
            "fires": int(totals.get("fired", 0)),
            "last_tick_epoch": last_tick_epoch,
            "last_event_epoch": last_event_epoch,
        }
    except Exception as exc:  # noqa: BLE001 — a query hiccup shouldn't crash the daemon
        print(f"[loop_dashboard] influx query failed: {exc}", file=sys.stderr)
        return None


# --- demo: a scripted sequence so `--dry --demo` shows the wheel spinning -------------------
_NOW = int(time.time())


def _snap(cron, event, backstop, ran, fresh, lag, free, shadow, tick_min_ago, event_min_ago):
    return {
        "ok": True, "by_cron": cron, "by_event": event, "by_backstop": backstop,
        "doit_executed": ran, "doit_uptodate": fresh, "fires": cron + event + backstop,
        "lag_s": lag, "budget_free_pct": free, "shadow": shadow,
        "last_tick_epoch": _NOW - tick_min_ago * 60,
        "last_event_epoch": (_NOW - event_min_ago * 60) if event_min_ago is not None else None,
    }


DEMO_SEQUENCE: list[dict | None] = [
    _snap(6, 31, 2, 0, 0, 0.1, 100, True, tick_min_ago=5, event_min_ago=20),   # shadow, healthy
    _snap(6, 31, 2, 0, 0, 0.1, 100, True, tick_min_ago=5, event_min_ago=20),   # unchanged → skip
    _snap(7, 44, 3, 12, 36, 0.2, 92, False, tick_min_ago=3, event_min_ago=2),  # live: event fresh
    _snap(1, 0, 0, 0, 0, 8.4, 88, False, tick_min_ago=1, event_min_ago=15),    # wedged → lag spike
    None,                                                                      # influx unreachable
    _snap(6, 38, 2, 9, 41, 0.3, 95, False, tick_min_ago=0, event_min_ago=4),   # recovered
]
DEMO_CAPTION = ["shadow ok", "no change", "live · event", "lag spike", "stopped", "recovered"]


# --- the job (shared by the standalone daemon and live_service's inner loop) ----------------
def make_job(url: str | None, *, dry: bool = False, state: str, interval: float = 60) -> Job:
    """One ferris-wheel tick as a live.Job — query Influx, redraw, reconcile."""
    dash = Dashboard(Poster(url, dry=dry), state_path=state, key="loop", source="loop-dashboard")
    cfg = load_config()
    last_good: dict | None = None

    def tick() -> str:
        nonlocal last_good
        snap = fetch_live(cfg)
        if snap is not None:
            last_good = snap
        return dash.tick(build_panel(snap, last_good))

    return Job("loop", interval, tick)


# --- loop ----------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="the #ops supervisor-loop ferris wheel (discokit)")
    ap.add_argument("--dry", action="store_true", help="print create/edit calls, post nothing")
    ap.add_argument("--demo", action="store_true", help="replay a scripted supervisor_tick sequence")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between demo ticks")
    ap.add_argument("--interval", type=int, default=60, help="poll seconds (live mode)")
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever (live mode)")
    ap.add_argument("--state", default=os.environ.get("LOOP_DASH_STATE", "/tmp/loop-dashboard.json"))
    args = ap.parse_args()

    url = config.webhook("OPS")  # dedicated DISCORD_WEBHOOK_OPS, else the general webhook → #ops
    if not args.dry and not url:
        print("[loop_dashboard] no DISCORD_WEBHOOK_OPS / DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    run = "DEMO" if args.demo else "live"
    print(f"[*] loop wheel — {run}{' · DRY' if args.dry else ''} · state={args.state}")

    if args.demo:
        dash = Dashboard(
            Poster(url, dry=args.dry), state_path=args.state, key="loop", source="loop-dashboard"
        )
        last_good: dict | None = None
        for i, snap in enumerate(DEMO_SEQUENCE):
            if snap is not None:
                last_good = snap
            print(f"\ntick {i}  ({DEMO_CAPTION[i]})")
            print(f"  └─ → {dash.tick(build_panel(snap, last_good))}")
            if args.delay and i < len(DEMO_SEQUENCE) - 1:
                time.sleep(args.delay)
        print("\n[done] one message, edited in place — no reposts.")
        return

    # same tick the live_service inner loop runs, just on a plain while/sleep here
    job = make_job(url, dry=args.dry, state=args.state, interval=args.interval)
    tick = 0
    while args.iterations == 0 or tick < args.iterations:
        print(f"[tick {tick}] {job.tick()}")
        tick += 1
        if args.iterations == 0 or tick < args.iterations:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
