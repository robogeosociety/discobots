#!/usr/bin/env python3
"""claude_heatmap — live Claude token-usage heatmap in #dashboards.

A GitHub-contributions-style heatmap of output tokens: rows = projects, columns =
the last N hourly buckets, color = intensity (⬛ idle → 🟦🟩🟨🟧 → 🟥 hot). Data is
the `tokens` measurement in the `claude_code` bucket (the series the claude-usage
Grafana dashboard reads). One self-editing Discord message per tick.

Migrated from observability-config/discord-claude-heatmap onto discokit (botmsg
edit-in-place + daemon watchdog). `parse_matrix()` + `render()` are pure and tested.

Env (injected by ops/run.sh at `docker run`):
  INFLUXDB_URL (default http://localhost:8086), INFLUXDB_ORG (home), INFLUXDB_TOKEN
  DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID   the #dashboards channel
  STATE_DIR (default /state), INTERVAL (default 60), HEATMAP_HOURS (default 12)
"""

import os
import pathlib
import sys
import urllib.request

from discokit.botmsg import BotChannel
from discokit.daemon import serve
from discokit.notify import StateFile

HEAT = ["⬛", "🟦", "🟩", "🟨", "🟧", "🟥"]   # 0 .. hot
ROWS = 8                                       # top-N projects shown
HOURS = int(os.environ.get("HEATMAP_HOURS", "12"))

INFLUX_URL = os.environ.get("INFLUXDB_URL", "http://localhost:8086").rstrip("/")
INFLUX_ORG = os.environ.get("INFLUXDB_ORG", "home")
CHANNEL = os.environ.get("DISCORD_CHANNEL_ID", "")
STATE_DIR = pathlib.Path(os.environ.get("STATE_DIR", "/state"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))

FLUX = (
    'from(bucket:"claude_code") |> range(start:-%dh) '
    '|> filter(fn:(r)=>r._measurement=="tokens" and r._field=="output_tokens") '
    '|> group(columns:["project"]) '
    '|> aggregateWindow(every:1h, fn:sum, createEmpty:true) '
    '|> keep(columns:["project","_time","_value"])'
) % HOURS


def parse_matrix(csv_text: str):
    """InfluxDB CSV → (matrix {project:{time:val}}, sorted_times list). Pure."""
    matrix, times = {}, set()
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
        proj = (row.get("project") or "").strip() or "—"
        t = (row.get("_time") or "").strip()
        if not t:
            continue
        v = (row.get("_value") or "").strip()
        try:
            val = float(v) if v else 0.0
        except ValueError:
            val = 0.0
        matrix.setdefault(proj, {})[t] = val
        times.add(t)
    return matrix, sorted(times)


def _human(n: float) -> str:
    for u in ("", "K", "M"):
        if n < 1000:
            return f"{n:.0f}{u}"
        n /= 1000
    return f"{n:.0f}B"


def _cell(v: float, mx: float) -> str:
    if v <= 0 or mx <= 0:
        return HEAT[0]
    frac = v / mx
    for i, thr in enumerate([0.02, 0.1, 0.3, 0.6]):
        if frac <= thr:
            return HEAT[i + 1]
    return HEAT[5]


def render(matrix: dict, times: list) -> str:
    """(matrix, times) → Discord heatmap message. Pure."""
    times = times[-HOURS:]
    totals = {p: sum(matrix[p].get(t, 0) for t in times) for p in matrix}
    top = sorted(totals, key=lambda p: -totals[p])[:ROWS]
    mx = max((matrix[p].get(t, 0) for p in top for t in times), default=0)
    lines = []
    for p in top:
        grid = "".join(_cell(matrix[p].get(t, 0), mx) for t in times)
        lines.append(f"{grid} {p[:22]} {_human(totals[p])}")
    grand = sum(totals.values())
    span = len(times)
    head = f"🔥 **Claude tokens — heatmap** · last {span}h hourly · {_human(grand)} out"
    scale = "scale ⬛ idle  " + "".join(HEAT[1:]) + "  hot"
    body = "\n".join(lines) if lines else "(no token activity in window)"
    return f"{head}\n\n{body}\n\n{scale}"


def fetch():
    token = os.environ.get("INFLUXDB_TOKEN") or ""
    if not token:
        raise SystemExit("INFLUXDB_TOKEN not set")
    req = urllib.request.Request(
        f"{INFLUX_URL}/api/v2/query?org={INFLUX_ORG}", data=FLUX.encode(), method="POST",
        headers={"Authorization": f"Token {token}", "Accept": "application/csv",
                 "Content-Type": "application/vnd.flux"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return parse_matrix(r.read().decode())


def main() -> None:
    if "--dry" in sys.argv:   # render the live snapshot, print it, post nothing
        m, t = fetch()
        print(render(m, t))
        return
    tok = os.environ.get("DISCORD_BOT_TOKEN") or sys.exit("DISCORD_BOT_TOKEN not set")
    if not CHANNEL:
        sys.exit("DISCORD_CHANNEL_ID not set")
    bc = BotChannel(tok, CHANNEL, StateFile(STATE_DIR / "state.json"), ua="discord-claude-heatmap/1.0")

    def tick() -> None:
        m, t = fetch()
        bc.upsert(render(m, t))

    sys.stderr.write(f"claude_heatmap: editing one #dashboards message every {INTERVAL}s\n")
    serve(tick, interval=INTERVAL, label="claude_heatmap", once="--once" in sys.argv)


if __name__ == "__main__":
    main()
