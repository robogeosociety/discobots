#!/usr/bin/env python3
"""
discord-ops-digest.py — Weekly Ops Digest for Discord

Queries InfluxDB for bucket health, job heartbeats, and notable events,
fetches deployment status from dev-status, and posts a summary embed
to a Discord webhook.

Intended to run Monday mornings via cron or Nomad periodic job.

Usage:
    python discord-ops-digest.py
    python discord-ops-digest.py --dry-run
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from influxdb_client import InfluxDBClient


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _read_dotenv(path: str) -> dict[str, str]:
    """Minimal .env parser — handles KEY=VALUE and KEY="VALUE" lines."""
    env = {}
    p = Path(path).expanduser()
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        env[key] = value
    return env


def load_config() -> dict:
    """Resolve configuration from env vars, falling back to .env files."""
    ask_dash_env = _read_dotenv("~/dev/observability/ask-dash/.env")
    grafana_env = _read_dotenv("~/dev/observability/grafana/.env")

    return {
        "influxdb_url": os.environ.get(
            "INFLUXDB_URL",
            ask_dash_env.get("INFLUXDB_URL", "http://localhost:8086"),
        ),
        "influxdb_token": os.environ.get(
            "INFLUXDB_TOKEN",
            ask_dash_env.get("INFLUXDB_TOKEN", ""),
        ),
        "influxdb_org": os.environ.get(
            "INFLUXDB_ORG",
            ask_dash_env.get("INFLUXDB_ORG", ""),
        ),
        "discord_webhook_url": os.environ.get(
            "DISCORD_WEBHOOK_URL",
            grafana_env.get("DISCORD_WEBHOOK_URL", ""),
        ),
        "dev_status_url": os.environ.get("DEV_STATUS_URL", "http://localhost:8077"),
    }


# ---------------------------------------------------------------------------
# InfluxDB queries
# ---------------------------------------------------------------------------

BUCKETS = ["telegraf", "weather", "ops"]


def query_bucket_health(client: InfluxDBClient, org: str) -> list[dict]:
    """Return last-write time for each bucket."""
    api = client.query_api()
    results = []
    for bucket in BUCKETS:
        flux = f"""
from(bucket: "{bucket}")
  |> range(start: -7d)
  |> last()
  |> keep(columns: ["_time"])
"""
        try:
            tables = api.query(flux, org=org)
            last_time = None
            for table in tables:
                for record in table.records:
                    t = record.get_time()
                    if last_time is None or t > last_time:
                        last_time = t
            results.append({"bucket": bucket, "last_write": last_time})
        except Exception as exc:
            results.append({"bucket": bucket, "last_write": None, "error": str(exc)})
    return results


def query_job_heartbeats(client: InfluxDBClient, org: str) -> list[dict]:
    """Return per-task success/failure counts over the past 7 days."""
    api = client.query_api()
    flux = """
from(bucket: "ops")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "heartbeat")
  |> filter(fn: (r) => r._field == "success")
  |> group(columns: ["task_name"])
  |> reduce(
       identity: {total: 0, ok: 0, fail: 0},
       fn: (r, accumulator) => ({
         total: accumulator.total + 1,
         ok:   if r._value == 1 then accumulator.ok + 1 else accumulator.ok,
         fail: if r._value != 1 then accumulator.fail + 1 else accumulator.fail
       })
     )
"""
    results = []
    try:
        tables = api.query(flux, org=org)
        for table in tables:
            for record in table.records:
                results.append({
                    "task_name": record.values.get("task_name", "unknown"),
                    "total": record.values.get("total", 0),
                    "ok": record.values.get("ok", 0),
                    "fail": record.values.get("fail", 0),
                })
    except Exception as exc:
        results.append({"task_name": "QUERY_ERROR", "error": str(exc)})
    return results


def query_notable_events(client: InfluxDBClient, org: str) -> list[dict]:
    """Return recent failures from the ops bucket."""
    api = client.query_api()
    flux = """
from(bucket: "ops")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "heartbeat")
  |> filter(fn: (r) => r._field == "success")
  |> filter(fn: (r) => r._value != 1)
  |> keep(columns: ["_time", "task_name", "step"])
  |> sort(columns: ["_time"], desc: true)
  |> limit(n: 15)
"""
    events = []
    try:
        tables = api.query(flux, org=org)
        for table in tables:
            for record in table.records:
                events.append({
                    "time": record.get_time().strftime("%Y-%m-%d %H:%M UTC"),
                    "task_name": record.values.get("task_name", "unknown"),
                    "step": record.values.get("step", ""),
                })
    except Exception as exc:
        events.append({"time": "N/A", "task_name": "QUERY_ERROR", "step": str(exc)})
    return events


# ---------------------------------------------------------------------------
# Dev-status
# ---------------------------------------------------------------------------

def fetch_deployments(url: str) -> dict | None:
    """GET the dev-status endpoint and return the JSON payload or None."""
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        print(f"[warn] dev-status unreachable: {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Embed builder
# ---------------------------------------------------------------------------

def _format_bucket_health(health: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    lines = []
    for entry in health:
        bucket = entry["bucket"]
        lt = entry.get("last_write")
        if lt is None:
            lines.append(f"\U0001F6A8 **{bucket}** — no data in 7d")
            continue
        age = now - lt
        hours = age.total_seconds() / 3600
        ts = lt.strftime("%Y-%m-%d %H:%M UTC")
        if hours > 72:
            emoji = "\U0001F6A8"  # alert
        elif hours > 24:
            emoji = "⚠️"  # warning
        else:
            emoji = "✅"  # ok
        lines.append(f"{emoji} **{bucket}** — last write {ts} ({hours:.0f}h ago)")
    return "\n".join(lines) or "No buckets checked."


def _format_jobs(jobs: list[dict]) -> str:
    if not jobs:
        return "No heartbeat data."
    lines = []
    for j in jobs:
        if "error" in j:
            lines.append(f"\U0001F6A8 Query error: {j['error']}")
            continue
        status = "✅" if j["fail"] == 0 else "⚠️"
        lines.append(
            f"{status} **{j['task_name']}** — "
            f"{j['ok']}/{j['total']} ok, {j['fail']} fail"
        )
    return "\n".join(lines)


def _format_deployments(data: dict | None) -> str:
    if data is None:
        return "\U0001F6A8 dev-status server unreachable"
    # Expect either a list or a dict of service->status
    services = data if isinstance(data, dict) else {}
    if isinstance(data, list):
        services = {s.get("name", s.get("service", "?")): s.get("status", "?") for s in data}
    if not services:
        return "No services reported."
    lines = []
    for name, info in services.items():
        status = info if isinstance(info, str) else info.get("status", "?")
        is_up = status.upper() in ("UP", "RUNNING", "HEALTHY", "OK")
        emoji = "✅" if is_up else "\U0001F534"
        lines.append(f"{emoji} **{name}** — {status}")
    return "\n".join(lines)


def _format_notable(events: list[dict]) -> str:
    if not events:
        return "No failures this week."
    lines = []
    for e in events:
        step = f" (step: {e['step']})" if e.get("step") else ""
        lines.append(f"• `{e['time']}` — **{e['task_name']}**{step}")
    return "\n".join(lines)


def build_embed(
    health: list[dict],
    jobs: list[dict],
    deployments: dict | None,
    notable: list[dict],
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "embeds": [
            {
                "title": "\U0001F4CA Weekly Ops Digest",
                "color": 0x3498DB,
                "timestamp": now.isoformat(),
                "footer": {"text": "ops-digest | ran on schedule"},
                "fields": [
                    {
                        "name": "\U0001F5C4️ Bucket Health",
                        "value": _format_bucket_health(health),
                        "inline": False,
                    },
                    {
                        "name": "\U0001F4BC Jobs (past 7d)",
                        "value": _format_jobs(jobs),
                        "inline": False,
                    },
                    {
                        "name": "\U0001F680 Deployments",
                        "value": _format_deployments(deployments),
                        "inline": False,
                    },
                    {
                        "name": "⚡ Notable Events",
                        "value": _format_notable(notable),
                        "inline": False,
                    },
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# Post to Discord
# ---------------------------------------------------------------------------

def post_to_discord(webhook_url: str, payload: dict) -> None:
    resp = httpx.post(webhook_url, json=payload, timeout=15)
    resp.raise_for_status()
    print(f"[ok] Discord webhook returned {resp.status_code}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly Ops Digest for Discord")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the embed JSON to stdout instead of posting to Discord",
    )
    args = parser.parse_args()

    cfg = load_config()

    if not cfg["influxdb_token"]:
        print("[error] INFLUXDB_TOKEN not set and not found in .env", file=sys.stderr)
        sys.exit(1)
    if not args.dry_run and not cfg["discord_webhook_url"]:
        print("[error] DISCORD_WEBHOOK_URL not set and not found in .env", file=sys.stderr)
        sys.exit(1)

    # --- InfluxDB queries ---
    client = InfluxDBClient(
        url=cfg["influxdb_url"],
        token=cfg["influxdb_token"],
        org=cfg["influxdb_org"],
    )

    print("[*] Querying bucket health ...")
    health = query_bucket_health(client, cfg["influxdb_org"])

    print("[*] Querying job heartbeats ...")
    jobs = query_job_heartbeats(client, cfg["influxdb_org"])

    print("[*] Querying notable events ...")
    notable = query_notable_events(client, cfg["influxdb_org"])

    client.close()

    # --- Dev-status ---
    print("[*] Fetching deployment status ...")
    deployments = fetch_deployments(cfg["dev_status_url"])

    # --- Build & send ---
    payload = build_embed(health, jobs, deployments, notable)

    if args.dry_run:
        print("\n--- DRY RUN — embed payload: ---")
        print(json.dumps(payload, indent=2, default=str))
    else:
        print("[*] Posting to Discord ...")
        post_to_discord(cfg["discord_webhook_url"], payload)

    print("[done]")


if __name__ == "__main__":
    main()
