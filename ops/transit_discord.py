#!/usr/bin/env python3
"""Fetch OneBusAway service alerts for watched routes and post to Discord."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OBA_API_BASE = "https://api.pugetsound.onebusaway.org/api/where"

WATCHED_ROUTES: dict[str, str] = {
    "1_100252": "Route 7",
    "1_100228": "Route 8",
    "1_100113": "Route 14",
    "1_102574": "Route 554",
    "40_100479": "1 Line",
    "40_2LINE": "2 Line",
}

# Agencies whose situations endpoint we poll
AGENCIES = ["1", "40"]  # 1 = King County Metro, 40 = Sound Transit

STATE_DIR = Path.home() / ".local" / "share" / "transit-discord"
STATE_FILE = STATE_DIR / "state.json"

STALE_DAYS = 7

# Severity -> embed colour mapping
SEVERITY_COLORS: dict[str, int] = {
    # Red
    "DETOUR": 0xE74C3C,
    "NO_SERVICE": 0xE74C3C,
    "SIGNIFICANT_DELAYS": 0xE74C3C,
    # Orange
    "construction": 0xE67E22,
    "weather": 0xE67E22,
    "MODERATE_DELAYS": 0xE67E22,
    # Yellow (default for anything else)
    "MINOR_DELAYS": 0xF1C40F,
}

DEFAULT_ALERT_COLOR = 0xF1C40F  # yellow / general info
CLEARED_COLOR = 0x2ECC71  # green

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_oba_api_key() -> str:
    return os.environ.get("OBA_API_KEY", "TEST")


def _get_discord_webhook_url() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        return url
    # Fall back to the grafana .env file
    env_path = Path.home() / "dev" / "observability" / "grafana" / ".env"
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISCORD_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def _color_for_situation(situation: dict) -> int:
    """Pick an embed colour based on severity or reason keywords."""
    severity = situation.get("severity", "")
    reason = situation.get("reason", "")

    # Check severity first
    if severity in SEVERITY_COLORS:
        return SEVERITY_COLORS[severity]

    # Check reason keywords
    reason_lower = reason.lower() if reason else ""
    for keyword in ("construction", "weather"):
        if keyword in reason_lower:
            return SEVERITY_COLORS[keyword]

    return DEFAULT_ALERT_COLOR


def _truncate(text: str, length: int = 200) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


def _affected_route_names(situation: dict) -> list[str]:
    """Return display names of watched routes affected by a situation."""
    names: list[str] = []
    consequences = situation.get("consequences", [])
    # Also check allAffectedRoutes / affectedRoutes at top level
    affected = situation.get("allAffects", [])
    if not affected:
        affected = situation.get("affects", [])

    route_ids: set[str] = set()

    # Parse consequences -> conditionDetails -> affectedEntity -> routeId
    for c in consequences:
        details = c.get("conditionDetails", {})
        entity = details.get("affectedEntity", {})
        rid = entity.get("routeId")
        if rid:
            route_ids.add(rid)

    # Parse affects (OBA v2 schema)
    for a in affected if isinstance(affected, list) else [affected]:
        if isinstance(a, dict):
            routes = a.get("routes", [])
            for r in routes if isinstance(routes, list) else [routes]:
                if isinstance(r, dict):
                    rid = r.get("routeId")
                    if rid:
                        route_ids.add(rid)

    # Also scan allAffects -> routeId patterns
    _scan_route_ids(situation, route_ids)

    for rid in sorted(route_ids):
        if rid in WATCHED_ROUTES:
            names.append(WATCHED_ROUTES[rid])

    return names


def _scan_route_ids(obj: dict | list, out: set[str]) -> None:
    """Recursively scan for routeId values in the situation dict."""
    if isinstance(obj, dict):
        if "routeId" in obj:
            out.add(obj["routeId"])
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _scan_route_ids(v, out)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _scan_route_ids(item, out)


# ---------------------------------------------------------------------------
# OBA fetching
# ---------------------------------------------------------------------------


def fetch_situations(client: httpx.Client, agency: str) -> list[dict]:
    """Fetch situations for an agency, returning the situation list."""
    api_key = _get_oba_api_key()
    url = f"{OBA_API_BASE}/situations-for-agency/{agency}.json"
    params = {"key": api_key}

    try:
        resp = client.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[warn] OBA request failed for agency {agency}: {exc}", file=sys.stderr)
        return []

    data = resp.json()
    situations = (
        data.get("data", {})
        .get("entry", {})
        .get("situations", [])
    )
    # Some OBA responses nest under "list" instead of "entry"
    if not situations:
        situations = (
            data.get("data", {})
            .get("list", [])
        )
    return situations if isinstance(situations, list) else []


def filter_watched(situations: list[dict]) -> dict[str, dict]:
    """Return {situation_id: situation} for situations touching watched routes."""
    matched: dict[str, dict] = {}
    watched_ids = set(WATCHED_ROUTES)

    for sit in situations:
        sit_id = sit.get("id")
        if not sit_id:
            continue

        # Collect every routeId mentioned anywhere in the situation
        route_ids: set[str] = set()
        _scan_route_ids(sit, route_ids)

        if route_ids & watched_ids:
            matched[sit_id] = sit

    return matched


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def load_state() -> dict:
    """Load persisted state. Returns {alert_id: {first_seen, ...}}."""
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def prune_stale(state: dict) -> dict:
    """Remove entries older than STALE_DAYS."""
    cutoff = time.time() - STALE_DAYS * 86400
    return {
        k: v for k, v in state.items()
        if v.get("first_seen", 0) > cutoff
    }


# ---------------------------------------------------------------------------
# Discord posting
# ---------------------------------------------------------------------------


def post_to_discord(
    client: httpx.Client,
    webhook_url: str,
    embeds: list[dict],
) -> None:
    """Post embeds to the Discord webhook. Never raises."""
    if not embeds:
        return
    # Discord allows max 10 embeds per message
    for i in range(0, len(embeds), 10):
        batch = embeds[i : i + 10]
        payload = {"embeds": batch}
        try:
            resp = client.post(webhook_url, json=payload, timeout=10)
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2)
                time.sleep(retry_after)
                client.post(webhook_url, json=payload, timeout=10)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] Discord post failed: {exc}", file=sys.stderr)


def build_new_alert_embed(situation: dict) -> dict:
    """Build a Discord embed for a new or updated alert."""
    summary = situation.get("summary", {})
    summary_text = summary.get("value", "") if isinstance(summary, dict) else str(summary)

    description = situation.get("description", {})
    desc_text = description.get("value", "") if isinstance(description, dict) else str(description)

    affected = _affected_route_names(situation)
    color = _color_for_situation(situation)
    severity = situation.get("severity", "unknown")
    reason = situation.get("reason", "")

    route_label = ", ".join(affected) if affected else "Multiple routes"

    embed: dict = {
        "title": f"Transit Alert — {route_label}",
        "color": color,
        "fields": [],
    }

    if summary_text:
        embed["fields"].append({"name": "Summary", "value": summary_text, "inline": False})
    if desc_text:
        embed["fields"].append({"name": "Details", "value": _truncate(desc_text), "inline": False})
    if affected:
        embed["fields"].append({"name": "Affected Routes", "value": ", ".join(affected), "inline": True})
    if severity:
        embed["fields"].append({"name": "Severity", "value": severity, "inline": True})
    if reason:
        embed["fields"].append({"name": "Reason", "value": reason, "inline": True})

    return embed


def build_cleared_embed(alert_id: str, state_entry: dict) -> dict:
    """Build a Discord embed for a resolved alert."""
    route_label = state_entry.get("route_label", "Transit")
    summary = state_entry.get("summary", "")
    return {
        "title": f"Cleared — {route_label}",
        "description": summary if summary else "A previous service alert has been resolved.",
        "color": CLEARED_COLOR,
    }


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def run(dry: bool = False) -> None:
    webhook_url = _get_discord_webhook_url()
    if not webhook_url and not dry:
        print("[error] No DISCORD_WEBHOOK_URL configured.", file=sys.stderr)
        sys.exit(1)

    state = prune_stale(load_state())

    with httpx.Client() as client:
        # Gather all situations across agencies
        current_alerts: dict[str, dict] = {}
        for agency in AGENCIES:
            situations = fetch_situations(client, agency)
            current_alerts.update(filter_watched(situations))

        current_ids = set(current_alerts)
        previous_ids = set(state)

        new_ids = current_ids - previous_ids
        cleared_ids = previous_ids - current_ids

        new_embeds: list[dict] = []
        cleared_embeds: list[dict] = []

        # Build embeds for new alerts
        for alert_id in sorted(new_ids):
            sit = current_alerts[alert_id]
            embed = build_new_alert_embed(sit)
            new_embeds.append(embed)

            # Persist metadata for later cleared messages
            affected = _affected_route_names(sit)
            summary = sit.get("summary", {})
            summary_text = summary.get("value", "") if isinstance(summary, dict) else str(summary)
            state[alert_id] = {
                "first_seen": time.time(),
                "route_label": ", ".join(affected) if affected else "Transit",
                "summary": summary_text,
            }

        # Build embeds for cleared alerts
        for alert_id in sorted(cleared_ids):
            entry = state.pop(alert_id, {})
            embed = build_cleared_embed(alert_id, entry)
            cleared_embeds.append(embed)

        # Ensure still-active alerts stay in state
        for alert_id in current_ids:
            if alert_id not in state:
                sit = current_alerts[alert_id]
                affected = _affected_route_names(sit)
                summary = sit.get("summary", {})
                summary_text = summary.get("value", "") if isinstance(summary, dict) else str(summary)
                state[alert_id] = {
                    "first_seen": time.time(),
                    "route_label": ", ".join(affected) if affected else "Transit",
                    "summary": summary_text,
                }

        all_embeds = new_embeds + cleared_embeds

        if dry:
            print(f"New alerts: {len(new_embeds)}")
            print(f"Cleared alerts: {len(cleared_embeds)}")
            for e in all_embeds:
                print(json.dumps(e, indent=2))
        elif all_embeds and webhook_url:
            post_to_discord(client, webhook_url, all_embeds)

        save_state(state)

    summary_parts = []
    if new_embeds:
        summary_parts.append(f"{len(new_embeds)} new")
    if cleared_embeds:
        summary_parts.append(f"{len(cleared_embeds)} cleared")
    if summary_parts:
        print(f"[info] Posted: {', '.join(summary_parts)}")
    else:
        print(f"[info] No changes. {len(current_ids)} active alert(s) tracked.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transit service alert -> Discord notifier")
    parser.add_argument("--dry", action="store_true", help="Print embeds instead of posting")
    args = parser.parse_args()
    run(dry=args.dry)
