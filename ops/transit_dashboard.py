#!/usr/bin/env python3
"""transit_dashboard — the #transit live status panel (collapse the alert churn).

The #transit channel is the loudest in the guild (~62 msgs/day): the alert
notifier (transit_discord.py) posts a fresh "Transit Alert — 2 Line" embed on
every FIRING and a "Cleared —" embed on every recovery, so a single flapping
line stacks dozens of discrete posts. This renders the SAME OneBusAway feed as
ONE Discord message that edits itself in place: a chip row of every watched line
(🟢 ok / 🟡🟠🔴 by worst active effect), with the actionable alert headers listed
below, down-first. One message, no reposts.

Reuses transit_discord's GTFS-Realtime fetch + watched-route list + effect
palette verbatim — this is the display layer only, not a second data path.

    # see the feel locally, no Discord, no OBA key, no deps beyond discokit:
    python3 ops/transit_dashboard.py --dry --demo

    # run live on the mini against the real #transit webhook:
    python3 ops/transit_dashboard.py --interval 60 --iterations 0   # 0 = forever
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# discokit (the package) and transit_discord both sit next to this file, in
# ops/ — and flat in /app inside the container. Put that dir on the path.
_OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(_OPS))

from discokit import config, graph, tokens  # noqa: E402
from discokit.dashboard import Dashboard  # noqa: E402
from discokit.live import Job  # noqa: E402
from discokit.poster import Poster  # noqa: E402

import transit_discord as td  # noqa: E402

# Watched lines in a stable display order (dict preserves insertion order).
LINES: list[str] = list(dict.fromkeys(td.WATCHED_ROUTES.values()))

# Severity ladder for "worst active effect per line". Higher = worse; the panel
# leads with the worst. Each tier carries the chip dot + embed colour reused
# from transit_discord's palette so the panel matches the alert embeds.
OK = ("ok", 0, tokens.OPERATIONAL.dot, tokens.OPERATIONAL.color)
YELLOW = ("minor", 1, tokens.DEGRADED.dot, td.YELLOW)
ORANGE = ("reduced", 2, "🟠", td.ORANGE)
RED = ("major", 3, tokens.CRITICAL.dot, td.RED)


def _classify(effect: str, header: str) -> tuple[str, int, str, int]:
    """Map a GTFS-RT alert to a severity tier, reusing transit_discord's rules."""
    color = td._color_for_alert(effect, header or "")
    if color == td.RED:
        return RED
    if color == td.ORANGE:
        return ORANGE
    return YELLOW


# --- rendering --------------------------------------------------------------
def build_panel(alerts: list[dict] | None, last_good: list[dict]) -> dict:
    """Build the panel embed. `alerts` None ⇒ OBA feed unreachable."""
    if alerts is None:
        st = tokens.DEGRADED
        body = f"{st.glyph} **transit feed unreachable** — showing last known state"
        alerts = last_good
        if not alerts:
            return {"embeds": [{"title": "🚈 transit", "description": body, "color": st.color}]}
    else:
        body = None

    # Worst active tier + a representative header per watched line.
    worst: dict[str, tuple[str, int, str, int]] = {name: OK for name in LINES}
    header: dict[str, str] = {}
    for a in alerts:
        tier = _classify(a.get("effect", ""), a.get("header", ""))
        for name in a.get("routes") or []:
            if name in worst and tier[1] > worst[name][1]:
                worst[name] = tier
                header[name] = a.get("header", "")

    # Down-first chip row: worst tiers lead so the actionable signal is on top.
    ordered = sorted(LINES, key=lambda n: -worst[n][1])
    chips = graph.chips([(name, worst[name][2]) for name in ordered])

    disrupted = [n for n in ordered if worst[n][1] > 0]
    overall = max((worst[n] for n in LINES), key=lambda t: t[1])
    up = len(LINES) - len(disrupted)

    if body is None:  # normal (reachable) render
        st_glyph = tokens.OPERATIONAL.glyph if not disrupted else tokens.CRITICAL.glyph
        body = f"{st_glyph} **{up}/{len(LINES)} lines clear**"
    lines_out = [body, chips]
    for n in disrupted:
        h = td._truncate(header.get(n, ""), 120)
        lines_out.append(f"{worst[n][2]} **{n}** — {h}" if h else f"{worst[n][2]} **{n}**")

    return {"embeds": [{"title": "🚈 transit — watched lines",
                        "description": "\n".join(lines_out),
                        "color": overall[3]}]}


# --- sources ----------------------------------------------------------------
def fetch_live() -> list[dict] | None:
    """Current watched-route alerts across agencies, or None if the feed is down."""
    import httpx  # lazy: only the live path needs httpx

    try:
        with httpx.Client() as client:
            out: list[dict] = []
            for agency in td.AGENCIES:
                out.extend(td.fetch_alerts(client, agency))
            return out
    except Exception as exc:  # noqa: BLE001 — a network hiccup shouldn't crash the loop
        print(f"[transit_dashboard] fetch failed: {exc}", file=sys.stderr)
        return None


# A scripted sequence for --demo: clear → 2 Line delayed → 1 Line down too →
# unreachable → recovered. One message, edited each step.
def _alert(routes: list[str], effect: str, header: str) -> dict:
    return {"id": "+".join(routes), "routes": routes, "effect": effect, "header": header}


DEMO_SEQUENCE: list[list[dict] | None] = [
    [],
    [_alert(["2 Line"], "SIGNIFICANT_DELAYS", "Delays between Bellevue and Redmond")],
    [_alert(["2 Line"], "SIGNIFICANT_DELAYS", "Delays between Bellevue and Redmond"),
     _alert(["1 Line"], "NO_SERVICE", "No service Intl Dist–Stadium, track work")],
    None,
    [],
]
DEMO_CAPTION = ["all clear", "2 Line delayed", "1 Line down too", "feed unreachable", "recovered"]


# --- the job (shared by the standalone daemon and live_service's inner loop) --
def make_job(url: str | None, *, dry: bool = False, state: str, interval: float = 60) -> Job:
    """One transit-panel tick as a live.Job — fetch, rebuild the panel, reconcile."""
    dash = Dashboard(Poster(url, dry=dry), state_path=state, key="transit", source="transit-dashboard")
    last_good: list[dict] = []

    def tick() -> str:
        nonlocal last_good
        alerts = fetch_live()
        if alerts is not None:
            last_good = alerts
        return dash.tick(build_panel(alerts, last_good))

    return Job("transit", interval, tick)


# --- loop -------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="the #transit live status panel (discokit)")
    ap.add_argument("--dry", action="store_true", help="print create/edit calls, post nothing")
    ap.add_argument("--demo", action="store_true", help="replay a scripted alert sequence")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between demo ticks")
    ap.add_argument("--interval", type=int, default=60, help="poll seconds (live mode)")
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever (live mode)")
    ap.add_argument("--state", default=os.environ.get("TRANSIT_DASH_STATE", "/tmp/transit-dashboard.json"))
    args = ap.parse_args()

    url = config.webhook("TRANSIT")
    if not args.dry and not url:
        print("[transit_dashboard] no DISCORD_WEBHOOK_TRANSIT / DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    print(f"[*] transit dashboard — {'DEMO' if args.demo else 'live'}"
          f"{' · DRY' if args.dry else ''} · state={args.state}")

    if args.demo:
        dash = Dashboard(Poster(url, dry=args.dry), state_path=args.state, key="transit", source="transit-dashboard")
        last_good: list[dict] = []
        for i, snapshot in enumerate(DEMO_SEQUENCE):
            if snapshot is not None:
                last_good = snapshot
            print(f"\ntick {i}  ({DEMO_CAPTION[i]})")
            print(f"  └─ → {dash.tick(build_panel(snapshot, last_good))}")
            if args.delay and i < len(DEMO_SEQUENCE) - 1:
                time.sleep(args.delay)
        print("\n[done] one message, edited in place — no reposts.")
        return

    job = make_job(url, dry=args.dry, state=args.state, interval=args.interval)
    tick = 0
    while args.iterations == 0 or tick < args.iterations:
        print(f"[tick {tick}] {job.tick()}")
        tick += 1
        if args.iterations == 0 or tick < args.iterations:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
