#!/usr/bin/env python3
"""Fetch GitHub activity for user 'tommyroar' and post new events to Discord.

Uses `gh api` CLI (subprocess) to fetch events; webhook resolution, posting,
and the seen-id gate come from discokit (config / poster / notify).
Designed to run on a launchd schedule alongside gh-board-sync.
"""

import json
import subprocess
import sys
from pathlib import Path

# discokit (the package) sits next to this file, in ops/ — and flat in /app
# inside the container. Put that dir on the path either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit import config, tokens  # noqa: E402
from discokit.notify import ChangeFeed, StateFile  # noqa: E402
from discokit.poster import Poster  # noqa: E402

GITHUB_USER = "tommyroar"
STATE_FILE = Path.home() / ".local" / "share" / "github-discord" / "state.json"

# Palette mapping: merged = the merge purple, opened = healthy, CI fail =
# critical, deploy = informational.
COLOR_MERGE = tokens.PURPLE
COLOR_PR_OPEN = tokens.OPERATIONAL.color
COLOR_CI_FAIL = tokens.CRITICAL.color
COLOR_DEPLOY = tokens.INFO.color


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


def main() -> None:
    dry = "--dry" in sys.argv

    webhook_url = config.webhook()
    if not webhook_url and not dry:
        print("[github-discord] No DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    events = fetch_events()
    if not events:
        print("[github-discord] No events fetched")
        return

    feed = ChangeFeed(StateFile(STATE_FILE))
    new_embeds: list[dict] = []

    for event in events:
        eid = event.get("id", "")
        if not feed.is_new(eid):
            continue
        embed = event_to_embed(event)
        if embed:
            new_embeds.append(embed)

    feed.save()

    if not new_embeds:
        print("[github-discord] No new relevant events")
        return

    print(f"[github-discord] Posting {len(new_embeds)} embed(s)")
    Poster(webhook_url, dry=dry).post(new_embeds)


if __name__ == "__main__":
    main()
