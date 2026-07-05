#!/usr/bin/env python3
"""Org-wide GitHub activity + the human-task board → the #dev channel.

The development heartbeat feed (the 2026-07-04 decision: #github → #dev).
Every 30 min it announces, each exactly once:

  • org events across robogeosociety — PRs opened/merged, issue events,
    releases (the CalVer bundles supervisor + obsidian-automations publish)
  • red CI on default branches (the Events API never delivers workflow runs,
    so these come from a per-repo Actions scan)
  • human-task board changes — issues labeled `human-task` opened / closed /
    commented, so Tommy's hands-on queue is pushed to him, not polled by him

Uses `gh api` CLI (subprocess) to fetch; webhook resolution, posting, and the
seen-id gate come from discokit (config / poster / notify). Events come from
the org dashboard feed (/users/{user}/events/orgs/{org}) — the plain
/users/{user}/events feed only carried the user's own public acts, so bot- and
agent-authored activity never showed up.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

# discokit (the package) sits next to this file, in ops/ — and flat in /app
# inside the container. Put that dir on the path either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit import config, tokens  # noqa: E402
from discokit.notify import ChangeFeed, StateFile  # noqa: E402
from discokit.poster import Poster  # noqa: E402

GITHUB_USER = "tommyroar"
GITHUB_ORG = "robogeosociety"
HUMAN_TASK_LABEL = "human-task"
# Only scan Actions runs for repos pushed this recently (bounds the API calls),
# and only announce runs younger than this (a fresh state volume must not
# replay history).
CI_ACTIVE_DAYS = 3
# Env-overridable so the supervisor (fleet-hosting F1) can point state at its
# own state dir instead of the container volume / ~/.local/share default.
STATE_DIR = Path(os.environ.get("GITHUB_STATE_DIR", str(Path.home() / ".local" / "share" / "github-discord")))
STATE_FILE = STATE_DIR / "state.json"  # seen event/run ids (ChangeFeed)
TASKS_FILE = STATE_DIR / "tasks.json"  # human-task board snapshot (StateFile)

# Palette mapping: merged = the merge purple, opened = healthy, CI fail =
# critical, release = maintenance purple, issues = informational, human tasks =
# degraded-yellow while they wait on Tommy, healthy-green when closed.
COLOR_MERGE = tokens.PURPLE
COLOR_PR_OPEN = tokens.OPERATIONAL.color
COLOR_CI_FAIL = tokens.CRITICAL.color
COLOR_RELEASE = tokens.MAINTENANCE.color
COLOR_ISSUE = tokens.INFO.color
COLOR_TASK_OPEN = tokens.DEGRADED.color
COLOR_TASK_DONE = tokens.OPERATIONAL.color
COLOR_TASK_NOTE = tokens.INFO.color


def gh_api(path: str, *, paginate: bool = False) -> list | dict | None:
    """GET a GitHub API path via `gh api`; None on any error.

    --paginate emits one JSON document per page — unparseable when
    concatenated — so it always rides with --slurp, which wraps the pages in
    one array.
    """
    cmd = ["gh", "api", path]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[github-discord] gh api {path} failed: {result.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"[github-discord] gh api {path}: unparseable output", file=sys.stderr)
        return None


def fetch_events() -> list[dict]:
    """Fetch the org dashboard feed — every actor's events in the org."""
    pages = gh_api(f"/users/{GITHUB_USER}/events/orgs/{GITHUB_ORG}", paginate=True)
    if not isinstance(pages, list):
        return []
    return [event for page in pages for event in (page if isinstance(page, list) else [page])]


def fetch_pr(repo: str, number: int) -> dict:
    """Hydrate a PR's real title/author/url via `gh api`.

    Events feeds return PullRequestEvents with a *stripped* `pull_request`
    (title/user/html_url all null) — so the embeds read "Untitled PR" /
    "unknown" with no link. Re-fetch the full PR to enrich them. {} on error.
    """
    return gh_api(f"/repos/{repo}/pulls/{number}") or {}


def _is_human_task(issue: dict) -> bool:
    return any(lbl.get("name") == HUMAN_TASK_LABEL for lbl in issue.get("labels", []))


def event_to_embed(event: dict, pr_fetcher=fetch_pr) -> dict | None:
    """Convert a GitHub event to a Discord embed dict, or None if not relevant.

    `pr_fetcher(repo, number) -> dict` hydrates stripped PR payloads; injectable for tests.
    """
    etype = event.get("type", "")
    payload = event.get("payload", {})
    repo_name = event.get("repo", {}).get("name", "unknown")

    if etype == "PullRequestEvent":
        action = payload.get("action", "")
        if action not in ("opened", "closed", "merged"):
            return None
        number = payload.get("number")
        pr = payload.get("pull_request") or {}
        # The events feed strips PR details — hydrate from the PR API.
        if number and (not pr.get("title") or not pr.get("html_url")):
            hydrated = pr_fetcher(repo_name, number)
            if hydrated:
                pr = hydrated

        title = pr.get("title") or "Untitled PR"
        author = (pr.get("user") or {}).get("login") or "unknown"
        html_url = pr.get("html_url") or (
            f"https://github.com/{repo_name}/pull/{number}" if number else ""
        )
        merged = bool(pr.get("merged")) or action == "merged"
        num = f"#{number} " if number else ""
        author_link = f"[@{author}](https://github.com/{author})" if author != "unknown" else author
        description = f"**Repo:** {repo_name}\n**Author:** {author_link}"

        if action in ("closed", "merged"):
            if not merged:
                return None  # closed-without-merge: not newsworthy
            return {
                "title": f"PR Merged: {num}{title}",
                "description": description,
                "url": html_url,
                "color": COLOR_MERGE,
            }
        elif action == "opened":
            return {
                "title": f"PR Opened: {num}{title}",
                "description": description,
                "url": html_url,
                "color": COLOR_PR_OPEN,
            }

    elif etype == "IssuesEvent":
        action = payload.get("action", "")
        if action not in ("opened", "closed", "reopened"):
            return None
        issue = payload.get("issue") or {}
        # Human-task issues are the board scan's beat (it sees every actor and
        # tracks comments too) — announcing them here would double-post.
        if _is_human_task(issue):
            return None
        number = issue.get("number")
        title = issue.get("title") or "Untitled issue"
        num = f"#{number} " if number else ""
        author = (issue.get("user") or {}).get("login") or "unknown"
        verb = {"opened": "Opened", "closed": "Closed", "reopened": "Reopened"}[action]
        return {
            "title": f"Issue {verb}: {num}{title}",
            "description": f"**Repo:** {repo_name}\n**Author:** [@{author}](https://github.com/{author})",
            "url": issue.get("html_url", ""),
            "color": COLOR_ISSUE if action != "closed" else tokens.UNKNOWN.color,
        }

    elif etype == "ReleaseEvent":
        if payload.get("action") != "published":
            return None
        release = payload.get("release") or {}
        tag = release.get("tag_name") or "untagged"
        name = release.get("name") or tag
        return {
            "title": f"🚀 Release: {repo_name} {name}",
            "description": f"**Tag:** {tag}",
            "url": release.get("html_url", ""),
            "color": COLOR_RELEASE,
        }

    return None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def scan_ci_failures(feed: ChangeFeed, fetch=gh_api, now: datetime | None = None) -> list[dict]:
    """Red CI on default branches, announced once per failed run id.

    The Events API carries no workflow runs, so this walks recently-pushed org
    repos and reads their Actions runs directly. Both the repo walk and the
    announce window are bounded to CI_ACTIVE_DAYS; `fetch` is injectable for tests.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=CI_ACTIVE_DAYS)
    repos = fetch(f"/orgs/{GITHUB_ORG}/repos?per_page=100&sort=pushed") or []
    embeds: list[dict] = []
    for repo in repos:
        if repo.get("archived"):
            continue
        pushed = _parse_ts(repo.get("pushed_at"))
        if pushed is None or pushed < cutoff:
            break  # sorted by pushed desc — everything after is older
        full = repo.get("full_name", "")
        branch = repo.get("default_branch", "main")
        runs = fetch(f"/repos/{full}/actions/runs?branch={branch}&status=completed&per_page=5") or {}
        for run in runs.get("workflow_runs", []):
            if run.get("conclusion") != "failure":
                continue
            created = _parse_ts(run.get("created_at"))
            if created is None or created < cutoff:
                continue
            if not feed.is_new(f"ci:{run.get('id')}"):
                continue
            embeds.append({
                "title": f"CI Failed: {run.get('name', 'workflow')}",
                "description": f"**Repo:** {full}\n**Branch:** {branch}",
                "url": run.get("html_url", ""),
                "color": COLOR_CI_FAIL,
            })
    return embeds


def scan_human_tasks(state: StateFile, fetch=gh_api) -> list[dict]:
    """Diff the org's human-task board (issues labeled `human-task`) and
    announce opened / closed / commented transitions.

    The board snapshot ({repo#n: {state, comments}}) persists via `state`. A
    fresh state file seeds silently (like the skills bot) so a recreated
    volume never re-announces the whole existing board.
    """
    q = quote_plus(f"org:{GITHUB_ORG} label:{HUMAN_TASK_LABEL}")
    data = fetch(f"/search/issues?q={q}&sort=updated&order=desc&per_page=50") or {}
    items = data.get("items")
    if items is None:  # fetch failed — keep the snapshot, try again next run
        return []

    doc = state.load()
    known: dict = doc.get("tasks", {})
    seeded = doc.get("seeded", False)
    embeds: list[dict] = []

    for it in items:
        repo = it.get("repository_url", "").rsplit("/repos/", 1)[-1]
        number = it.get("number")
        key = f"{repo}#{number}"
        cur = {"state": it.get("state", "open"), "comments": it.get("comments", 0)}
        prev = known.get(key)
        title = it.get("title") or "Untitled task"
        url = it.get("html_url", "")
        ref = f"{repo.rsplit('/', 1)[-1]}#{number}"
        if seeded:
            if prev is None and cur["state"] == "open":
                embeds.append({
                    "title": f"🧭 Human task opened: {ref} {title}",
                    "description": f"**Repo:** {repo}",
                    "url": url,
                    "color": COLOR_TASK_OPEN,
                })
            elif prev and prev.get("state") == "open" and cur["state"] == "closed":
                embeds.append({
                    "title": f"✅ Human task closed: {ref} {title}",
                    "description": f"**Repo:** {repo}",
                    "url": url,
                    "color": COLOR_TASK_DONE,
                })
            elif prev and cur["comments"] > prev.get("comments", 0):
                delta = cur["comments"] - prev.get("comments", 0)
                embeds.append({
                    "title": f"💬 Human task activity: {ref} {title}",
                    "description": f"**Repo:** {repo}\n**New comments:** {delta}",
                    "url": url,
                    "color": COLOR_TASK_NOTE,
                })
        known[key] = cur

    doc["tasks"] = known
    doc["seeded"] = True
    state.save(doc)
    if not seeded and items:
        print(f"[github-discord] seeded human-task board silently ({len(items)} task(s))")
    return embeds


def main() -> None:
    dry = "--dry" in sys.argv

    webhook_url = config.webhook()
    if not webhook_url and not dry:
        print("[github-discord] No DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    feed = ChangeFeed(StateFile(STATE_FILE))
    new_embeds: list[dict] = []

    for event in fetch_events():
        eid = event.get("id", "")
        if not feed.is_new(eid):
            continue
        embed = event_to_embed(event)
        if embed:
            new_embeds.append(embed)

    new_embeds.extend(scan_ci_failures(feed))
    feed.save()
    new_embeds.extend(scan_human_tasks(StateFile(TASKS_FILE)))

    if not new_embeds:
        print("[github-discord] No new relevant events")
        return

    print(f"[github-discord] Posting {len(new_embeds)} embed(s)")
    Poster(webhook_url, dry=dry).post(new_embeds)


if __name__ == "__main__":
    main()
