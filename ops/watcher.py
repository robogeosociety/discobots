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
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

# discokit (the package) sits next to this file, in ops/ — and flat in /app
# inside the container. Put that dir on the path either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit import config, tokens  # noqa: E402
from discokit.poster import Poster  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    return {
        "dev_status_url": os.environ.get("DEV_STATUS_URL", "http://localhost:8077"),
        "discord_webhook_url": config.webhook() or "",
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
    # Current dev-status shape: {"deployments": [{"name", "up": 0|1, ...}]}.
    if isinstance(data, dict) and isinstance(data.get("deployments"), list):
        for entry in data["deployments"]:
            name = entry.get("name", entry.get("service", "unknown"))
            services[name] = "UP" if entry.get("up") else "DOWN"
    # Alternate shapes: a bare list of {name,status}, or a dict service->status.
    elif isinstance(data, list):
        for entry in data:
            name = entry.get("name", entry.get("service", "unknown"))
            services[name] = _normalize_status(entry.get("status", "UNKNOWN"))
    elif isinstance(data, dict):
        for name, info in data.items():
            if isinstance(info, str):
                status = info
            elif isinstance(info, dict):
                status = info.get("status", "UNKNOWN")
            else:
                continue  # skip scalar metadata fields (total, generated_unix, …)
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


def _post_embed(poster: Poster, title: str, description: str, color: int) -> None:
    poster.post(
        [
            {
                "title": title,
                "description": description,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "footer": {"text": "ops-watcher"},
            }
        ]
    )


def notify_service_up(poster: Poster, name: str) -> None:
    _post_embed(
        poster,
        f"{tokens.OPERATIONAL.glyph} {name} is UP",
        f"Service **{name}** is now healthy.",
        tokens.OPERATIONAL.color,
    )


def notify_service_down(poster: Poster, name: str) -> None:
    _post_embed(
        poster,
        f"{tokens.CRITICAL.glyph} {name} is DOWN",
        f"Service **{name}** has been down for 2 consecutive polls.",
        tokens.CRITICAL.color,
    )


def notify_service_disappeared(poster: Poster, name: str) -> None:
    _post_embed(
        poster,
        f"\U0001F7E0 {name} disappeared",
        f"Service **{name}** is no longer reported by dev-status.",
        tokens.ORANGE,
    )


def notify_server_unreachable(poster: Poster) -> None:
    _post_embed(
        poster,
        f"{tokens.DEGRADED.glyph} dev-status unreachable",
        "The dev-status server at `localhost:8077` is not responding.",
        tokens.DEGRADED.color,
    )


def notify_server_recovered(poster: Poster) -> None:
    _post_embed(
        poster,
        f"{tokens.OPERATIONAL.glyph} dev-status recovered",
        "The dev-status server is responding again.",
        tokens.OPERATIONAL.color,
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
        poster: Poster,
    ) -> None:
        # --- Handle dev-status being unreachable ---
        if current is None:
            # Only alert once we've had a successful poll. A failure before that
            # is a startup/connectivity warm-up (e.g. a freshly (re)started
            # container whose host.docker.internal path isn't ready yet), not an
            # outage — alerting there would false-fire on every restart.
            if self.initialised and not self.server_unreachable_alerted:
                notify_server_unreachable(poster)
                self.server_unreachable_alerted = True
            return

        # If server was unreachable and is now back, notify
        if self.server_unreachable_alerted:
            notify_server_recovered(poster)
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
                notify_service_up(poster, name)
            else:
                # New but already down — note it, start debounce
                self.down_counter[name] = 1

        # --- Disappeared services ---
        for name in known_names - current_names:
            notify_service_disappeared(poster, name)
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
                    notify_service_down(poster, name)
                    self.services[name] = "DOWN"
                    self.down_counter.pop(name, None)
                # else: don't update services yet, wait for second poll

            elif prev == "UP" and now == "UP":
                # Reset any stale debounce counter
                self.down_counter.pop(name, None)

            elif prev == "DOWN" and now == "UP":
                notify_service_up(poster, name)
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

    poster = Poster(cfg["discord_webhook_url"], dry=dry_run)
    url = cfg["dev_status_url"]
    state = WatcherState()

    print(f"[*] ops-watcher starting — polling {url} every {interval}s")
    if dry_run:
        print("[*] DRY-RUN mode: embeds will be printed, not posted")

    while not _shutdown:
        current = fetch_services(url)
        state.process(current, poster)
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
