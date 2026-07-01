#!/usr/bin/env python3
"""ops_dashboard — the #ops dynamic dashboard (Phase-2 discokit spike).

Renders the dev-status service readout as ONE Discord message that edits itself
in place, instead of re-posting. Proves the edit-in-place feel end to end.

    # see the whole feel locally, no Discord, no deps:
    python3 ops/ops_dashboard.py --dry --demo

    # run live on the mini against the real #ops webhook:
    python3 ops/ops_dashboard.py --interval 30 --iterations 0   # 0 = forever

Live mode reuses watcher.py's dev-status parsing; --demo replays a scripted
sequence (all-up → unchanged → a service down → source unreachable → recovered)
so you can watch a single message evolve.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# discokit (the package) and watcher.py both sit next to this file, in ops/ —
# and flat in /app inside the container. Put that dir on the path either way.
_OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(_OPS))

from discokit import config, tokens  # noqa: E402
from discokit.dashboard import Dashboard  # noqa: E402
from discokit.poster import Poster  # noqa: E402


# --- rendering --------------------------------------------------------------
MAX_ROWS = 40  # keep the embed description well under Discord's 4096-char cap


def _rows(items: list[tuple[str, bool]]) -> str:
    """Render name→up rows (down-first already applied), capped with a +N tail."""
    lines = [f"{tokens.up_down(is_up).glyph} {name}" for name, is_up in items]
    if len(lines) > MAX_ROWS:
        hidden = len(lines) - MAX_ROWS
        lines = lines[:MAX_ROWS] + [f"… +{hidden} more"]
    return "\n".join(lines)


def build_panel(services: dict[str, bool] | None, last_good: dict[str, bool]) -> dict:
    """Build the dashboard embed. `services` None ⇒ dev-status unreachable."""
    if services is None:
        st = tokens.DEGRADED
        body = f"{st.glyph} **dev-status unreachable** — showing last known state"
        if last_good:
            body += "\n" + _rows(sorted(last_good.items(), key=lambda kv: kv[1]))
        return {"embeds": [{"title": "🖥️ dev status", "description": body, "color": st.color}]}

    up = sum(1 for v in services.values() if v)
    total = len(services)
    st = tokens.OPERATIONAL if up == total else tokens.CRITICAL
    # Down first — the actionable signal leads.
    rows = _rows(sorted(services.items(), key=lambda kv: kv[1]))
    body = f"{st.glyph} **{up}/{total} up**\n" + rows
    return {"embeds": [{"title": "🖥️ dev status", "description": body, "color": st.color}]}


# --- sources ----------------------------------------------------------------
def fetch_live(url: str) -> dict[str, bool] | None:
    """Reuse watcher.py's dev-status parsing; return {name: is_up} or None."""
    import watcher  # lazy: only the live path needs httpx

    parsed = watcher.fetch_services(url)  # {name: "UP"|"DOWN"} or None
    if parsed is None:
        return None
    return {name: state == "UP" for name, state in parsed.items()}


DEMO_SEQUENCE: list[dict[str, bool] | None] = [
    {"web": True, "api": True, "db": True, "cache": True},   # create
    {"web": True, "api": True, "db": True, "cache": True},   # unchanged → skip
    {"web": True, "api": False, "db": True, "cache": True},  # api down → edit
    None,                                                    # unreachable → edit (degraded)
    {"web": True, "api": True, "db": True, "cache": True},   # recovered → edit
]

DEMO_CAPTION = ["all up", "no change", "api down", "unreachable", "recovered"]


# --- loop -------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="the #ops dynamic dashboard (discokit spike)")
    ap.add_argument("--dry", action="store_true", help="print create/edit calls, post nothing")
    ap.add_argument("--demo", action="store_true", help="replay a scripted state sequence")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between demo ticks (pace a live demo)")
    ap.add_argument("--interval", type=int, default=30, help="poll seconds (live mode)")
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever (live mode)")
    ap.add_argument("--dev-status-url", default=os.environ.get("DEV_STATUS_URL", "http://localhost:8077"))
    ap.add_argument("--state", default=os.environ.get("OPS_DASH_STATE", "/tmp/ops-dashboard.json"))
    args = ap.parse_args()

    url = config.webhook("OPS")
    if not args.dry and not url:
        print("[ops_dashboard] no DISCORD_WEBHOOK_OPS / DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    dash = Dashboard(
        Poster(url, dry=args.dry),
        state_path=args.state,
        key="ops",
        source="ops-dashboard",
    )
    last_good: dict[str, bool] = {}

    print(f"[*] ops dashboard — {'DEMO' if args.demo else args.dev_status_url}"
          f"{' · DRY' if args.dry else ''} · state={args.state}")

    if args.demo:
        for i, snapshot in enumerate(DEMO_SEQUENCE):
            if snapshot is not None:
                last_good = snapshot
            print(f"\ntick {i}  ({DEMO_CAPTION[i]})")
            result = dash.tick(build_panel(snapshot, last_good))
            print(f"  └─ → {result}")
            if args.delay and i < len(DEMO_SEQUENCE) - 1:
                time.sleep(args.delay)
        print("\n[done] one message, edited in place — no reposts.")
        return

    # live: poll dev-status, reconcile the single message
    tick = 0
    while args.iterations == 0 or tick < args.iterations:
        snapshot = fetch_live(args.dev_status_url)
        if snapshot is not None:
            last_good = snapshot
        result = dash.tick(build_panel(snapshot, last_good))
        print(f"[tick {tick}] {result}")
        tick += 1
        if args.iterations == 0 or tick < args.iterations:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
