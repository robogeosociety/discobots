#!/usr/bin/env python3
"""orbmem — live OrbStack per-container memory treemap in #dashboards.

The container sibling of `minimem`: same self-editing emoji treemap, but the data
is per-container memory from InfluxDB (`docker_container_mem` in the `system`
bucket — Telegraf's docker input), the SAME source as the Grafana "Memory map —
container usage" panel, so the Discord and Grafana treemaps can't disagree.

Migrated from observability-config/discord-orbstack-mem onto discokit (treemap /
botmsg / daemon watchdog). `parse_rows()` + `render()` are pure and tested.

Env (injected by ops/run.sh at `docker run`):
  INFLUXDB_URL (default http://localhost:8086), INFLUXDB_ORG (home), INFLUXDB_TOKEN
  DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID   the #dashboards channel
  STATE_DIR (default /state), INTERVAL (default 60)
"""

import os
import pathlib
import sys
import urllib.request

from discokit.botmsg import BotChannel
from discokit.daemon import serve
from discokit.notify import StateFile
from discokit.treemap import EMOJI, grid_and_legend, human_mb

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086").rstrip("/")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "home")
CHANNEL = os.environ.get("DISCORD_CHANNEL_ID", "")
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))

FLUX = (
    'from(bucket:"system") |> range(start:-5m) '
    '|> filter(fn:(r)=>r._measurement=="docker_container_mem" and r._field=="usage") '
    '|> last() |> keep(columns:["container_name","_value"]) '
    '|> group() |> sort(columns:["_value"], desc:true)'
)


def parse_rows(csv_text: str) -> list[tuple[str, float]]:
    """InfluxDB CSV (annotated or plain) → [(container_name, usage_mb)] desc. Pure."""
    out = []
    header = None
    for raw in csv_text.splitlines():
        line = raw.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        cols = line.split(",")
        if header is None:
            header = cols
            continue
        row = dict(zip(header, cols))
        name = row.get("container_name", "").strip()
        val = row.get("_value", "").strip()
        if not name or not val:
            continue
        try:
            out.append((name, float(val) / 1048576.0))   # bytes → MiB
        except ValueError:
            continue
    out.sort(key=lambda kv: -kv[1])
    return out


def render(rows: list[tuple[str, float]]) -> str:
    """[(container, usage_mb)] → Discord message string. Pure."""
    items = rows[:len(EMOJI)]
    total_used = sum(v for _, v in rows)
    count = len(rows)
    body, legend = grid_and_legend(items)
    head = f"{count} containers · {human_mb(total_used)} used"
    return f"🐳 **OrbStack containers — memory** — {head}\n\n{body}\n\n{legend}"


def fetch_rows() -> list[tuple[str, float]]:
    token = os.environ.get("INFLUXDB_TOKEN") or ""
    if not token:
        raise SystemExit("INFLUXDB_TOKEN not set")
    req = urllib.request.Request(
        f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}", data=FLUX.encode(), method="POST",
        headers={"Authorization": f"Token {token}", "Accept": "application/csv",
                 "Content-Type": "application/vnd.flux"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_rows(r.read().decode())


def main() -> None:
    if "--dry" in sys.argv:   # render the live snapshot, print it, post nothing
        print(render(fetch_rows()))
        return
    tok = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN not set")
    if not CHANNEL:
        sys.exit("DISCORD_CHANNEL_ID not set")
    bc = BotChannel(tok, CHANNEL, StateFile(STATE_DIR / "state.json"), ua="discord-orbstack-mem/1.0")

    def tick() -> None:
        bc.upsert(render(fetch_rows()))

    sys.stderr.write(f"orbmem: editing one #dashboards message every {INTERVAL}s\n")
    serve(tick, interval=INTERVAL, label="orbmem", once="--once" in sys.argv)


if __name__ == "__main__":
    main()
