#!/usr/bin/env python3
"""
discord-ops-watcher.py — Deployment Status Watcher for Discord

Long-running daemon that polls the dev-status server every 30 seconds
and posts to Discord when service state changes are detected.

Usage:
    python discord-ops-watcher.py
    python discord-ops-watcher.py --dry-run
    python discord-ops-watcher.py --interval 15
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx


# ---------------------------------------------------------------------------
# Configuration
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
    grafana_env = _read_dotenv("~/dev/observability/grafana/.env")
    return {
        "dev_status_url": os.environ.get("DEV_STATUS_URL", "http://localhost:8077"),
        "discord_webhook_url": os.environ.get(
            "DISCORD_WEBHOOK_URL",
            grafana_env.get("DISCORD_WEBHOOK_URL", ""),
        ),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_status(raw: str) -> str:
    """Collapse status strings into UP or DOWN."""
    return "UP" if raw.upper() in ("UP", "RUNNING", "HEALTHY", "OK") else "DOWN"


def _parse_services(data) -> dict[str, str]:
    """Turn dev-status response into {name: "UP"|"DOWN"} map."""
    services: dict[str, str] = {}
    if isinstance(data, dict):
        for name, info in data.items():
            status = info if isinstance(info, str) else info.get("status", "UNKNOWN")
            services[name] = _normalize_status(status)
    elif isinstance(data, list):
        for entry in data:
            name = entry.get("name", entry.get("service", "unknown"))
            status = entry.get("status", "UNKNOWN")
            services[name] = _normalize_status(status)
    return services


def fetch_services(url: str) -> Optional[dict[str, str]]:
    """GET dev-status and return normalised service map, or None on error."""
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return _parse_services(resp.json())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Discord posting
# ---------------------------------------------------------------------------

COLOR_GREEN = 0x2ECC71
COLOR_RED = 0xE74C3C
COLOR_ORANGE = 0xE67E22
COLOR_GREY = 0x95A5A6


def _post_embed(
    webhook_url: str,
    title: str,
    description: str,
    color: int,
    dry_run: bool = False,
) -> None:
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "ops-watcher"},
            }
        ]
    }
    if dry_run:
        print(f"[dry-run] {json.dumps(payload, indent=2)}")
        return
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        print(f"[ok] Discord {resp.status_code}: {title}")
    except Exception as exc:
        print(f"[error] Discord post failed: {exc}", file=sys.stderr)


def notify_service_up(webhook: str, name: str, dry_run: bool) -> None:
    _post_embed(
        webhook,
        f"✅ {name} is UP",
        f"Service **{name}** is now healthy.",
        COLOR_GREEN,
        dry_run,
    )


def notify_service_down(webhook: str, name: str, dry_run: bool) -> None:
    _post_embed(
        webhook,
        f"\U0001F534 {name} is DOWN",
        f"Service **{name}** has been down for 2 consecutive polls.",
        COLOR_RED,
        dry_run,
    )


def notify_service_disappeared(webhook: str, name: str, dry_run: bool) -> None:
    _post_embed(
        webhook,
        f"\U0001F7E0 {name} disappeared",
        f"Service **{name}** is no longer reported by dev-status.",
        COLOR_ORANGE,
        dry_run,
    )


def notify_server_unreachable(webhook: str, dry_run: bool) -> None:
    _post_embed(
        webhook,
        "⚠️ dev-status unreachable",
        "The dev-status server at `localhost:8077` is not responding.",
        COLOR_GREY,
        dry_run,
    )


def notify_server_recovered(webhook: str, dry_run: bool) -> None:
    _post_embed(
        webhook,
        "✅ dev-status recovered",
        "The dev-status server is responding again.",
        COLOR_GREEN,
        dry_run,
    )


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class WatcherState:
    """Tracks known services and debounce counters."""

    def __init__(self) -> None:
        # service_name -> "UP" | "DOWN"
        self.services: dict[str, str] = {}
        # service_name -> consecutive DOWN poll count (for debounce)
        self.down_counter: dict[str, int] = {}
        # Whether we have already alerted that dev-status is unreachable
        self.server_unreachable_alerted: bool = False
        # Set to True once we have received at least one successful poll
        self.initialised: bool = False

    def process(
        self,
        current: Optional[dict[str, str]],
        webhook: str,
        dry_run: bool,
    ) -> None:
        # --- Handle dev-status being unreachable ---
        if current is None:
            # Only alert once we've had a successful poll. A failure before that
            # is a startup/connectivity warm-up (e.g. a freshly (re)started
            # container whose host.docker.internal path isn't ready yet), not an
            # outage — alerting there would false-fire on every restart.
            if self.initialised and not self.server_unreachable_alerted:
                notify_server_unreachable(webhook, dry_run)
                self.server_unreachable_alerted = True
            return

        # If server was unreachable and is now back, notify
        if self.server_unreachable_alerted:
            notify_server_recovered(webhook, dry_run)
            self.server_unreachable_alerted = False

        # --- First poll: seed state silently ---
        if not self.initialised:
            self.services = dict(current)
            self.down_counter = {}
            self.initialised = True
            up_count = sum(1 for s in current.values() if s == "UP")
            down_count = len(current) - up_count
            print(
                f"[init] Tracking {len(current)} services "
                f"({up_count} up, {down_count} down)"
            )
            return

        current_names = set(current.keys())
        known_names = set(self.services.keys())

        # --- New services ---
        for name in current_names - known_names:
            self.services[name] = current[name]
            if current[name] == "UP":
                notify_service_up(webhook, name, dry_run)
            else:
                # New but already down — note it, start debounce
                self.down_counter[name] = 1

        # --- Disappeared services ---
        for name in known_names - current_names:
            notify_service_disappeared(webhook, name, dry_run)
            del self.services[name]
            self.down_counter.pop(name, None)

        # --- Existing services: detect transitions ---
        for name in current_names & known_names:
            prev = self.services[name]
            now = current[name]

            if prev == "UP" and now == "DOWN":
                # Start debounce
                self.down_counter[name] = self.down_counter.get(name, 0) + 1
                if self.down_counter[name] >= 2:
                    notify_service_down(webhook, name, dry_run)
                    self.services[name] = "DOWN"
                    self.down_counter.pop(name, None)
                # else: don't update services yet, wait for second poll

            elif prev == "UP" and now == "UP":
                # Reset any stale debounce counter
                self.down_counter.pop(name, None)

            elif prev == "DOWN" and now == "UP":
                notify_service_up(webhook, name, dry_run)
                self.services[name] = "UP"
                self.down_counter.pop(name, None)

            # DOWN -> DOWN: already alerted, nothing to do


# ---------------------------------------------------------------------------
# Daemon loop
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_signal(signum, _frame):
    global _shutdown
    sig_name = signal.Signals(signum).name
    print(f"\n[*] Received {sig_name}, shutting down ...")
    _shutdown = True


def run_loop(cfg: dict, interval: int, dry_run: bool) -> None:
    global _shutdown

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    webhook = cfg["discord_webhook_url"]
    url = cfg["dev_status_url"]
    state = WatcherState()

    print(f"[*] ops-watcher starting — polling {url} every {interval}s")
    if dry_run:
        print("[*] DRY-RUN mode: embeds will be printed, not posted")

    while not _shutdown:
        current = fetch_services(url)
        state.process(current, webhook, dry_run)
        # Sleep in small increments so we respond to signals promptly
        deadline = time.monotonic() + interval
        while time.monotonic() < deadline and not _shutdown:
            time.sleep(0.5)

    print("[*] ops-watcher stopped.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Deployment Status Watcher for Discord")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print embeds to stdout instead of posting to Discord",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Poll interval in seconds (default: 30)",
    )
    args = parser.parse_args()

    cfg = load_config()

    if not args.dry_run and not cfg["discord_webhook_url"]:
        print(
            "[error] DISCORD_WEBHOOK_URL not set and not found in grafana/.env",
            file=sys.stderr,
        )
        sys.exit(1)

    run_loop(cfg, args.interval, args.dry_run)


if __name__ == "__main__":
    main()
