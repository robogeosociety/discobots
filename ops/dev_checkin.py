#!/usr/bin/env python3
"""Daily development check-in for #dev — a template-rendered status update.

Runs from the github container's crontab at 08:00 (container TZ =
America/Los_Angeles, set by run.sh) — the crontab line IS the cadence config.
One embed in the voice of a morning status update:

  • PRs merged across robogeosociety since the last check-in
  • default-branch CI health for recently-active repos (✅ / 🔴 chips)
  • the open human-task queue (issues labeled `human-task`)
  • upcoming scheduled events (fleet-sync Mondays 07:17 UTC)

Template-rendered by design; a `claude -p` narrative pass (the
obsidian-automations #92/#93 precedent) is a documented follow-on, not this
script's job. Shares gh_api / the repo walk with github_discord.py.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit import config, tokens  # noqa: E402
from discokit.notify import StateFile  # noqa: E402
from discokit.poster import Poster  # noqa: E402
from github_discord import (  # noqa: E402
    GITHUB_ORG,
    HUMAN_TASK_LABEL,
    STATE_DIR,
    _parse_ts,
    gh_api,
)

CHECKIN_FILE = STATE_DIR / "checkin.json"  # last check-in timestamp
# CI chips cover repos pushed within this window; the digest is a health
# readout, not an archaeology dig.
CI_HEALTH_DAYS = 7
MAX_LIST = 8  # cap per section so the embed stays one screenful


def merged_prs_since(since: datetime, fetch=gh_api) -> list[dict]:
    """PRs merged org-wide since `since`, newest first."""
    stamp = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    q = quote_plus(f"org:{GITHUB_ORG} is:pr is:merged merged:>={stamp}")
    data = fetch(f"/search/issues?q={q}&sort=updated&order=desc&per_page=50") or {}
    return data.get("items") or []


def open_human_tasks(fetch=gh_api) -> list[dict]:
    q = quote_plus(f"org:{GITHUB_ORG} label:{HUMAN_TASK_LABEL} state:open")
    data = fetch(f"/search/issues?q={q}&sort=updated&order=desc&per_page=50") or {}
    return data.get("items") or []


def ci_health(fetch=gh_api, now: datetime | None = None) -> list[tuple[str, str]]:
    """(repo name, ✅/🔴/⚪ chip) for each org repo pushed within CI_HEALTH_DAYS,
    from the latest completed default-branch run."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CI_HEALTH_DAYS)
    repos = fetch(f"/orgs/{GITHUB_ORG}/repos?per_page=100&sort=pushed") or []
    chips: list[tuple[str, str]] = []
    for repo in repos:
        if repo.get("archived"):
            continue
        pushed = _parse_ts(repo.get("pushed_at"))
        if pushed is None or pushed < cutoff:
            break  # sorted by pushed desc
        full = repo.get("full_name", "")
        branch = repo.get("default_branch", "main")
        runs = fetch(f"/repos/{full}/actions/runs?branch={branch}&status=completed&per_page=1") or {}
        latest = (runs.get("workflow_runs") or [{}])[0]
        conclusion = latest.get("conclusion")
        if conclusion is None:
            chip = "⚪"  # no CI on this repo
        elif conclusion == "success":
            chip = "✅"
        else:
            chip = "🔴"
        chips.append((repo.get("name", full), chip))
    return chips


def next_fleet_sync(now: datetime | None = None) -> datetime:
    """The next scheduled fleet-sync: Mondays 07:17 UTC (supervisor repo)."""
    now = now or datetime.now(timezone.utc)
    candidate = now.replace(hour=7, minute=17, second=0, microsecond=0)
    days_ahead = (0 - now.weekday()) % 7  # Monday = 0
    candidate += timedelta(days=days_ahead)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def _issue_line(it: dict) -> str:
    repo = it.get("repository_url", "").rsplit("/", 1)[-1]
    return f"• [{repo}#{it.get('number')}]({it.get('html_url', '')}) {it.get('title', '')}"


def build_checkin(since: datetime, now: datetime | None = None, fetch=gh_api) -> dict:
    """Render the check-in embed from live data (fetch injectable for tests)."""
    now = now or datetime.now(timezone.utc)
    sections: list[str] = []

    merged = merged_prs_since(since, fetch=fetch)
    lines = [_issue_line(it) for it in merged[:MAX_LIST]]
    if len(merged) > MAX_LIST:
        lines.append(f"… and {len(merged) - MAX_LIST} more")
    sections.append(
        f"**Merged since last check-in ({len(merged)})**\n" + ("\n".join(lines) or "• a quiet stretch — nothing merged")
    )

    chips = ci_health(fetch=fetch, now=now)
    red = [f"{c} {name}" for name, c in chips if c == "🔴"]
    green = sum(1 for _, c in chips if c == "✅")
    ci_line = f"**CI on main ({green}/{len(chips)} green)**\n" + (
        "\n".join(f"• {r}" for r in red) if red else "• all active lanes green"
    )
    sections.append(ci_line)

    tasks = open_human_tasks(fetch=fetch)
    tlines = [_issue_line(it) for it in tasks[:MAX_LIST]]
    if len(tasks) > MAX_LIST:
        tlines.append(f"… and {len(tasks) - MAX_LIST} more")
    sections.append(
        f"**Open human tasks ({len(tasks)})**\n" + ("\n".join(tlines) or "• queue clear 🎉")
    )

    sync = next_fleet_sync(now)
    sections.append(f"**Upcoming**\n• fleet-sync <t:{int(sync.timestamp())}:F> (supervisor, mini-fleet runner)")

    return {
        "title": f"☕ Dev check-in — {now.astimezone().strftime('%a %b %-d')}",
        "description": "\n\n".join(sections),
        "color": tokens.INFO.color,
    }


def main() -> None:
    dry = "--dry" in sys.argv

    webhook_url = config.webhook()
    if not webhook_url and not dry:
        print("[dev-checkin] No DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    state = StateFile(CHECKIN_FILE)
    doc = state.load()
    now = datetime.now(timezone.utc)
    since = _parse_ts(doc.get("last_run")) or (now - timedelta(hours=24))

    embed = build_checkin(since, now=now)
    Poster(webhook_url, dry=dry).post([embed])

    if not dry:
        doc["last_run"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        state.save(doc)
    print(f"[dev-checkin] posted (window since {since.isoformat()})")


if __name__ == "__main__":
    main()
