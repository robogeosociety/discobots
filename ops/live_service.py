#!/usr/bin/env python3
"""live_service — the discobots inner loop: every #ops dashboard on ONE asyncio loop.

Consolidates the dashboard daemons (dashboard / loop / embed — previously three
containers, three processes, three poll loops — plus the tommybot chat panel)
into one supervised process running one asyncio event loop (discokit.live).
Each dashboard keeps its own cadence; a slow tick runs in a worker thread and
never delays the others; a throwing tick is logged and retried next round. This
is the level-2 application loop of the fleet-hosting plan — the Phase-4 gateway
attaches to this same loop.

    # one tick of each dashboard, printed not posted (parity check, no deps):
    python3 ops/live_service.py --dry --once

    # run live on the mini (state paths + intervals via env, see below):
    python3 ops/live_service.py

Env knobs (defaults mirror the standalone daemons):
    OPS/LOOP/EMBED/CHAT_DASH_STATE                     state file paths
    OPS/LOOP/EMBED/CHAT_DASH_INTERVAL                  poll seconds
    DEV_STATUS_URL, TOMMYBOT_CACHE_DIR, TOMMYBOT_LIVE_FILE   source locations
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# discokit (the package) and the dashboard modules sit next to this file, in
# ops/ — and flat in /app inside the container. Put that dir on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chat_dashboard  # noqa: E402
import embed_dashboard  # noqa: E402
import loop_dashboard  # noqa: E402
import ops_dashboard  # noqa: E402
from discokit import config, live  # noqa: E402


def build_jobs(*, dry: bool) -> list[live.Job]:
    url = config.webhook("OPS")  # dedicated DISCORD_WEBHOOK_OPS, else the general webhook
    if not dry and not url:
        print("[live_service] no DISCORD_WEBHOOK_OPS / DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    env = os.environ.get
    if dry:
        # Never touch the real state in dry mode: a dry tick that saved a new
        # content signature would make the live daemon skip its next real edit
        # (matters when `just dry live` execs inside the running container,
        # where the *_DASH_STATE envs point at the adopted volumes).
        def env(key, default):  # noqa: F811 — deliberate dry-mode shadow
            if key.endswith("_STATE"):
                return f"/tmp/dry-{key.lower()}.json"
            return os.environ.get(key, default)

    return [
        ops_dashboard.make_job(
            url, dry=dry,
            state=env("OPS_DASH_STATE", "/tmp/ops-dashboard.json"),
            interval=float(env("OPS_DASH_INTERVAL", "30")),
            dev_status_url=env("DEV_STATUS_URL", "http://localhost:8077"),
        ),
        loop_dashboard.make_job(
            url, dry=dry,
            state=env("LOOP_DASH_STATE", "/tmp/loop-dashboard.json"),
            interval=float(env("LOOP_DASH_INTERVAL", "60")),
            bus_url=env("BUS_URL", None),
        ),
        embed_dashboard.make_job(
            url, dry=dry,
            state=env("EMBED_DASH_STATE", "/tmp/embed-dashboard.json"),
            interval=float(env("EMBED_DASH_INTERVAL", "300")),
            db_dir=env("TOMMYBOT_CACHE_DIR", "/mnt/tommybot-cache"),
        ),
        chat_dashboard.make_job(
            url, dry=dry,
            state=env("CHAT_DASH_STATE", "/tmp/chat-dashboard.json"),
            interval=float(env("CHAT_DASH_INTERVAL", "5")),
            live_file=env("TOMMYBOT_LIVE_FILE", "/mnt/tommybot-cache/live.json"),
        ),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="all #ops dashboards on one asyncio inner loop")
    ap.add_argument("--dry", action="store_true", help="print create/edit calls, post nothing")
    ap.add_argument("--once", action="store_true", help="tick each dashboard once, then exit")
    args = ap.parse_args()

    jobs = build_jobs(dry=args.dry)
    if args.once:
        for job in jobs:
            print(f"[{job.name} · once] {job.tick()}")
        return
    live.run_jobs(jobs)


if __name__ == "__main__":
    main()
