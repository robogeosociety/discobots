#!/usr/bin/env python3
"""Fetch OneBusAway service alerts for watched routes and post to Discord.

Source: OneBusAway's **GTFS-Realtime** service-alerts feed,
`/api/gtfs_realtime/alerts-for-agency/<agencyId>.pb` (protobuf). The old REST
`situations-for-agency` call this used was not a real OBA method and 404'd; the
GTFS-RT endpoint is the supported agency-wide alerts source.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from google.transit import gtfs_realtime_pb2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# GTFS-Realtime alerts feed (protobuf), per agency: …/alerts-for-agency/<id>.pb
OBA_GTFS_RT_BASE = "https://api.pugetsound.onebusaway.org/api/gtfs_realtime"

# Watched routes, keyed by "<agencyId>_<gtfsRouteId>" — the same composite the
# GTFS-RT feed yields from each informed_entity's (agency_id, route_id).
WATCHED_ROUTES: dict[str, str] = {
    "1_100252": "Route 7",
    "1_100228": "Route 8",
    "1_100113": "Route 14",
    "1_102574": "Route 554",
    "40_100479": "1 Line",
    "40_2LINE": "2 Line",
}

# Agencies whose alerts feed we poll
AGENCIES = ["1", "40"]  # 1 = King County Metro, 40 = Sound Transit

STATE_DIR = Path.home() / ".local" / "share" / "transit-discord"
STATE_FILE = STATE_DIR / "state.json"

STALE_DAYS = 7

# GTFS-RT Effect -> embed colour
RED = 0xE74C3C
ORANGE = 0xE67E22
YELLOW = 0xF1C40F
CLEARED_COLOR = 0x2ECC71  # green

EFFECT_RED = {"NO_SERVICE", "SIGNIFICANT_DELAYS", "DETOUR"}
EFFECT_ORANGE = {"REDUCED_SERVICE", "MODIFIED_SERVICE", "STOP_MOVED"}

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


def _truncate(text: str, length: int = 200) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= length:
        return text
    return text[: length - 1] + "…"


def _format_duration(seconds: float) -> str:
    """Human 'was active for' string, e.g. '2d 3h', '4h', '<1m'."""
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:  # minutes only matter at sub-day resolution
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "<1m"


def _translated(ts) -> str:
    """First translation of a GTFS-RT TranslatedString, or ""."""
    if ts and ts.translation:
        return ts.translation[0].text or ""
    return ""


def _color_for_alert(effect: str, header: str) -> int:
    """Embed colour from the GTFS-RT effect, with a header-keyword fallback."""
    if effect in EFFECT_RED:
        return RED
    if effect in EFFECT_ORANGE:
        return ORANGE
    h = header.lower()
    if any(k in h for k in ("detour", "no service", "significant delay")):
        return RED
    if any(k in h for k in ("delay", "closed", "closure", "reroute", "suspend", "relocat")):
        return ORANGE
    return YELLOW


def _watched_routes_for(alert) -> list[str]:
    """Display names of watched routes touched by a GTFS-RT alert (sorted, unique)."""
    names: set[str] = set()
    for ie in alert.informed_entity:
        if not ie.route_id:
            continue
        # The feed gives the bare route_id + agency_id; recompose the watched key.
        key = f"{ie.agency_id}_{ie.route_id}" if ie.agency_id else ie.route_id
        if key in WATCHED_ROUTES:
            names.add(WATCHED_ROUTES[key])
    return sorted(names)


# ---------------------------------------------------------------------------
# OBA GTFS-Realtime fetching
# ---------------------------------------------------------------------------


def fetch_alerts(client: httpx.Client, agency: str) -> list[dict]:
    """Fetch the agency's GTFS-RT alerts, normalised to dicts, watched routes only.

    Each returned dict: {id, header, description, effect, cause, url, routes}.
    """
    url = f"{OBA_GTFS_RT_BASE}/alerts-for-agency/{agency}.pb"
    params = {"key": _get_oba_api_key()}
    try:
        resp = client.get(url, params=params, timeout=15)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"[warn] OBA alerts request failed for agency {agency}: {exc}", file=sys.stderr)
        return []

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(resp.content)
    except Exception as exc:  # noqa: BLE001 — malformed protobuf shouldn't crash the poll
        print(f"[warn] could not parse GTFS-RT feed for agency {agency}: {exc}", file=sys.stderr)
        return []

    out: list[dict] = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        routes = _watched_routes_for(alert)
        if not routes:
            continue  # only alerts touching a watched route
        effect = gtfs_realtime_pb2.Alert.Effect.Name(alert.effect) if alert.effect else ""
        cause = gtfs_realtime_pb2.Alert.Cause.Name(alert.cause) if alert.cause else ""
        out.append(
            {
                "id": entity.id,
                "header": _translated(alert.header_text),
                "description": _translated(alert.description_text),
                "effect": "" if effect in ("", "UNKNOWN_EFFECT") else effect,
                "cause": "" if cause in ("", "UNKNOWN_CAUSE") else cause,
                "url": _translated(alert.url),
                "routes": routes,
            }
        )
    return out


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
    return {k: v for k, v in state.items() if v.get("first_seen", 0) > cutoff}


# ---------------------------------------------------------------------------
# Discord posting
# ---------------------------------------------------------------------------


def post_to_discord(client: httpx.Client, webhook_url: str, embeds: list[dict]) -> None:
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


def build_new_alert_embed(alert: dict) -> dict:
    """Build a Discord embed for a new or updated alert."""
    routes = alert.get("routes") or []
    route_label = ", ".join(routes) if routes else "Multiple routes"

    embed: dict = {
        "title": f"Transit Alert — {route_label}",
        "color": _color_for_alert(alert.get("effect", ""), alert.get("header", "")),
        "fields": [],
    }
    if alert.get("url"):
        embed["url"] = alert["url"]
    if alert.get("header"):
        embed["fields"].append({"name": "Summary", "value": _truncate(alert["header"]), "inline": False})
    if alert.get("description"):
        embed["fields"].append({"name": "Details", "value": _truncate(alert["description"]), "inline": False})
    if routes:
        embed["fields"].append({"name": "Affected Routes", "value": ", ".join(routes), "inline": True})
    if alert.get("effect"):
        embed["fields"].append({"name": "Effect", "value": alert["effect"].replace("_", " ").title(), "inline": True})
    if alert.get("cause"):
        embed["fields"].append({"name": "Cause", "value": alert["cause"].replace("_", " ").title(), "inline": True})
    return embed


def build_cleared_embed(alert_id: str, state_entry: dict) -> dict:
    """Build a Discord embed for a resolved alert.

    Mirrors the richness of a new-alert embed (routes / effect / how long it was
    active) instead of just a title. Every field is optional so entries persisted
    before enrichment still render cleanly."""
    route_label = state_entry.get("route_label", "Transit")
    summary = state_entry.get("summary", "")
    embed: dict = {
        "title": f"Cleared — {route_label}",
        "description": summary if summary else "A previous service alert has been resolved.",
        "color": CLEARED_COLOR,
        "fields": [],
    }
    if state_entry.get("url"):
        embed["url"] = state_entry["url"]
    routes = state_entry.get("routes") or []
    if routes:
        embed["fields"].append({"name": "Affected Routes", "value": ", ".join(routes), "inline": True})
    if state_entry.get("effect"):
        embed["fields"].append(
            {"name": "Was", "value": state_entry["effect"].replace("_", " ").title(), "inline": True}
        )
    first_seen = state_entry.get("first_seen")
    if first_seen:
        embed["fields"].append(
            {"name": "Active for", "value": _format_duration(time.time() - first_seen), "inline": True}
        )
    # An embed timestamp renders as a relative "cleared just now" in Discord.
    embed["timestamp"] = datetime.now(timezone.utc).isoformat()
    return embed


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def _state_entry(alert: dict, first_seen: float | None = None) -> dict:
    routes = alert.get("routes") or []
    return {
        "first_seen": first_seen if first_seen is not None else time.time(),
        "route_label": ", ".join(routes) if routes else "Transit",
        "summary": alert.get("header", ""),
        # Persisted so the cleared embed can be as rich as the new-alert one.
        "effect": alert.get("effect", ""),
        "url": alert.get("url", ""),
        "routes": routes,
    }


def run(dry: bool = False) -> None:
    webhook_url = _get_discord_webhook_url()
    if not webhook_url and not dry:
        print("[error] No DISCORD_WEBHOOK_URL configured.", file=sys.stderr)
        sys.exit(1)

    state = prune_stale(load_state())

    with httpx.Client() as client:
        # Gather watched-route alerts across agencies
        current_alerts: dict[str, dict] = {}
        for agency in AGENCIES:
            for alert in fetch_alerts(client, agency):
                current_alerts[alert["id"]] = alert

        current_ids = set(current_alerts)
        previous_ids = set(state)

        new_ids = current_ids - previous_ids
        cleared_ids = previous_ids - current_ids

        new_embeds: list[dict] = []
        cleared_embeds: list[dict] = []

        # New alerts
        for alert_id in sorted(new_ids):
            alert = current_alerts[alert_id]
            new_embeds.append(build_new_alert_embed(alert))
            state[alert_id] = _state_entry(alert)

        # Cleared alerts
        for alert_id in sorted(cleared_ids):
            entry = state.pop(alert_id, {})
            cleared_embeds.append(build_cleared_embed(alert_id, entry))

        # Refresh still-active alerts each run so the persisted fields stay current
        # (backfilling enrichment onto pre-existing entries), preserving the original
        # first_seen that powers the cleared embed's "Active for" duration.
        for alert_id in current_ids:
            prev = state.get(alert_id)
            first_seen = prev.get("first_seen") if isinstance(prev, dict) else None
            state[alert_id] = _state_entry(current_alerts[alert_id], first_seen=first_seen)

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
