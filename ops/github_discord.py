#!/usr/bin/env python3
"""Fetch GitHub activity for user 'tommyroar' and post new events to Discord.

Uses `gh api` CLI (subprocess) to fetch events and httpx for webhook posts.
Tracks seen event IDs in a local state file to avoid duplicate notifications.
Designed to run on a launchd schedule alongside gh-board-sync.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

GITHUB_USER = "tommyroar"
STATE_DIR = Path.home() / ".local" / "share" / "github-discord"
STATE_FILE = STATE_DIR / "state.json"
MAX_SEEN_IDS = 500
DISCORD_EMBEDS_PER_REQUEST = 10

# Embed colours
COLOR_MERGE = 0x6F42C1   # purple
COLOR_PR_OPEN = 0x2ECC71  # green
COLOR_CI_FAIL = 0xE74C3C  # red
COLOR_DEPLOY = 0x3498DB   # blue


def get_webhook_url() -> str | None:
    """Return the Discord webhook URL from env or fallback .env file."""
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if url:
        return url

    fallback = Path.home() / "dev" / "observability" / "grafana" / ".env"
    if fallback.exists():
        for line in fallback.read_text().splitlines():
            line = line.strip()
            if line.startswith("DISCORD_WEBHOOK_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def load_seen_ids() -> set[str]:
    """Load previously seen event IDs from state file."""
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text())
        return set(data.get("seen_ids", []))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen_ids(seen_ids: set[str]) -> None:
    """Persist seen event IDs, capped at MAX_SEEN_IDS most recent."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep only the most recent IDs to prevent unbounded growth.
    # Since we can't know true recency from a set, just cap the size.
    ids_list = list(seen_ids)
    if len(ids_list) > MAX_SEEN_IDS:
        ids_list = ids_list[-MAX_SEEN_IDS:]
    STATE_FILE.write_text(json.dumps({"seen_ids": ids_list}))


def fetch_events() -> list[dict]:
    """Fetch public events for the GitHub user via `gh api`."""
    result = subprocess.run(
        ["gh", "api", f"/users/{GITHUB_USER}/events", "--paginate"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[github-discord] gh api failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print("[github-discord] Failed to parse gh api output", file=sys.stderr)
        return []


def event_to_embed(event: dict) -> dict | None:
    """Convert a GitHub event to a Discord embed dict, or None if not relevant."""
    etype = event.get("type", "")
    payload = event.get("payload", {})
    repo_name = event.get("repo", {}).get("name", "unknown")

    if etype == "PullRequestEvent":
        pr = payload.get("pull_request", {})
        action = payload.get("action", "")
        title = pr.get("title", "Untitled PR")
        author = pr.get("user", {}).get("login", "unknown")
        html_url = pr.get("html_url", "")

        if action == "closed" and pr.get("merged"):
            return {
                "title": f"PR Merged: {title}",
                "description": f"**Repo:** {repo_name}\n**Author:** {author}",
                "url": html_url,
                "color": COLOR_MERGE,
            }
        elif action == "opened":
            return {
                "title": f"PR Opened: {title}",
                "description": f"**Repo:** {repo_name}\n**Author:** {author}",
                "url": html_url,
                "color": COLOR_PR_OPEN,
            }

    elif etype == "WorkflowRunEvent":
        workflow_run = payload.get("workflow_run", {})
        conclusion = workflow_run.get("conclusion", "")
        if conclusion == "failure":
            workflow_name = workflow_run.get("name", "Unknown workflow")
            branch = workflow_run.get("head_branch", "unknown")
            run_url = workflow_run.get("html_url", "")
            return {
                "title": f"CI Failed: {workflow_name}",
                "description": f"**Repo:** {repo_name}\n**Branch:** {branch}",
                "url": run_url,
                "color": COLOR_CI_FAIL,
            }

    elif etype in ("DeploymentEvent", "CreateEvent"):
        if etype == "DeploymentEvent":
            deployment = payload.get("deployment", {})
            environment = deployment.get("environment", "unknown")
            status = deployment.get("task", "deploy")
            deploy_url = deployment.get("url", "")
            return {
                "title": f"Deployment: {repo_name}",
                "description": f"**Environment:** {environment}\n**Status:** {status}",
                "url": deploy_url,
                "color": COLOR_DEPLOY,
            }

    return None


def post_embeds(webhook_url: str, embeds: list[dict], dry: bool = False) -> None:
    """Post embeds to Discord in batches of up to 10."""
    for i in range(0, len(embeds), DISCORD_EMBEDS_PER_REQUEST):
        batch = embeds[i : i + DISCORD_EMBEDS_PER_REQUEST]
        payload = {"embeds": batch}

        if dry:
            print(f"[dry-run] Would post {len(batch)} embed(s):")
            for embed in batch:
                print(f"  - {embed.get('title', '(no title)')}")
            continue

        try:
            resp = httpx.post(webhook_url, json=payload, timeout=10)
            if resp.status_code >= 400:
                print(
                    f"[github-discord] Discord returned {resp.status_code}: {resp.text}",
                    file=sys.stderr,
                )
        except httpx.HTTPError as exc:
            print(f"[github-discord] Discord request failed: {exc}", file=sys.stderr)


def main() -> None:
    dry = "--dry" in sys.argv

    webhook_url = get_webhook_url()
    if not webhook_url and not dry:
        print("[github-discord] No DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    events = fetch_events()
    if not events:
        print("[github-discord] No events fetched")
        return

    seen_ids = load_seen_ids()
    new_embeds: list[dict] = []

    for event in events:
        eid = event.get("id", "")
        if not eid or eid in seen_ids:
            continue
        seen_ids.add(eid)
        embed = event_to_embed(event)
        if embed:
            new_embeds.append(embed)

    save_seen_ids(seen_ids)

    if not new_embeds:
        print("[github-discord] No new relevant events")
        return

    print(f"[github-discord] Posting {len(new_embeds)} embed(s)")
    post_embeds(webhook_url or "", new_embeds, dry=dry)


if __name__ == "__main__":
    main()
