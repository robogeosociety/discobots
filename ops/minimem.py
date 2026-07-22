#!/usr/bin/env python3
"""minimem — live Mac-mini memory treemap as a self-editing #dashboards message.

Pulls the SAME per-process snapshot that feeds the Grafana mac-system treemap
(dev-status collector at :8077/processes.json, reached from the container via
host.docker.internal), renders it as a colored-square emoji treemap (mobile-safe —
Discord ANSI color is desktop-only), and edits one Discord message every tick.

Migrated from observability-config/discord-mini-mem onto discokit: the treemap is
`discokit.treemap`, the edit-in-place transport is `discokit.botmsg`, and the tick
loop is `discokit.daemon.serve` — whose per-tick watchdog fixes the DNS-hang wedge
that silently froze the standalone bot for three days.

Env (injected by ops/run.sh at `docker run`):
  PROCESSES_URL       default http://host.docker.internal:8077/processes.json
  DISCORD_BOT_TOKEN   OpsBot token (authors/edits the message)
  DISCORD_CHANNEL_ID  the #dashboards channel
  STATE_DIR           default /state — state.json {message_id} for restart re-use
  INTERVAL            seconds between edits (default 60)
"""

import json
import os
import pathlib
import sys
import urllib.request

from discokit.botmsg import BotChannel
from discokit.daemon import serve
from discokit.notify import StateFile
from discokit.treemap import EMOJI, grid_and_legend, human_mb

PROCESSES_URL = os.environ.get("PROCESSES_URL", "http://host.docker.internal:8077/processes.json")
CHANNEL = os.environ.get("DISCORD_CHANNEL_ID", "")
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))

NAMES = {"OrbStack Helper": "OrbStack", "Google Chrome Helper": "Chrome",
         "Obsidian Helper (Renderer)": "Obsidian", "com.apple.WebKit.WebContent": "WebKit"}


def render(data: dict) -> str:
    """processes.json dict → the Discord message string. Pure."""
    procs = [(p["name"], float(p["mem_mb"])) for p in data.get("processes", [])
             if p.get("kind") == "proc"]
    procs.sort(key=lambda kv: -kv[1])
    items = [(NAMES.get(n, n), v) for n, v in procs[:len(EMOJI)]]
    total = float(data.get("total_mem_mb", 0)) or 1.0
    idle = sum(float(p["mem_mb"]) for p in data.get("processes", []) if p.get("kind") == "idle")
    used = max(0.0, total - idle)

    body, legend = grid_and_legend(items)
    pct = 100 * used / total
    head = f"used {human_mb(used)}/{human_mb(total)} ({pct:.0f}%) · free {human_mb(idle)}"
    return f"🖥️ **Mac mini memory** — {head}\n\n{body}\n\n{legend}"


def fetch() -> dict:
    with urllib.request.urlopen(PROCESSES_URL, timeout=15) as r:
        return json.loads(r.read())


def main() -> None:
    if "--dry" in sys.argv:   # render the live snapshot, print it, post nothing
        print(render(fetch()))
        return
    tok = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN not set")
    if not CHANNEL:
        sys.exit("DISCORD_CHANNEL_ID not set")
    bc = BotChannel(tok, CHANNEL, StateFile(STATE_DIR / "state.json"), ua="discord-mini-mem/1.0")

    def tick() -> None:
        bc.upsert(render(fetch()))

    sys.stderr.write(f"minimem: editing one #dashboards message every {INTERVAL}s from {PROCESSES_URL}\n")
    serve(tick, interval=INTERVAL, label="minimem", once="--once" in sys.argv)


if __name__ == "__main__":
    main()
