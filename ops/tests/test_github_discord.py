"""Hermetic tests for github_discord.event_to_embed PR enrichment (no network).

The /users/{user}/events feed strips PR payloads; these pin that we hydrate them
via the injected pr_fetcher and never emit "Untitled PR" / "unknown" when the PR
is resolvable.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import github_discord as gd  # noqa: E402

# A stripped PullRequestEvent exactly as the user-events feed returns it.
STRIPPED_OPENED = {
    "type": "PullRequestEvent",
    "repo": {"name": "robogeosociety/discobots"},
    "payload": {"action": "opened", "number": 42,
                "pull_request": {"title": None, "user": None, "html_url": None}},
}
STRIPPED_MERGED = {
    "type": "PullRequestEvent",
    "repo": {"name": "robogeosociety/wikipedia-local"},
    "payload": {"action": "merged", "number": 3,
                "pull_request": {"title": None, "user": None, "html_url": None}},
}

FAKE_PRS = {
    ("robogeosociety/discobots", 42): {"title": "Add heatmap", "user": {"login": "tommyroar"},
                                  "html_url": "https://github.com/robogeosociety/discobots/pull/42"},
    ("robogeosociety/wikipedia-local", 3): {"title": "Standardize", "user": {"login": "tommyroar"},
                                       "html_url": "https://github.com/robogeosociety/wikipedia-local/pull/3",
                                       "merged": True},
}


def fake_fetcher(repo, number):
    return FAKE_PRS.get((repo, number), {})


def test_opened_pr_is_hydrated_not_untitled():
    e = gd.event_to_embed(STRIPPED_OPENED, pr_fetcher=fake_fetcher)
    assert e["title"] == "PR Opened: #42 Add heatmap"       # real title + number, not "Untitled PR"
    assert "[@tommyroar](https://github.com/tommyroar)" in e["description"]
    assert e["url"] == "https://github.com/robogeosociety/discobots/pull/42"
    assert e["color"] == gd.COLOR_PR_OPEN


def test_merged_action_is_handled_and_hydrated():
    e = gd.event_to_embed(STRIPPED_MERGED, pr_fetcher=fake_fetcher)
    assert e["title"] == "PR Merged: #3 Standardize"        # action "merged" now posts (was silently dropped)
    assert e["color"] == gd.COLOR_MERGE
    assert e["url"].endswith("/pull/3")


def test_url_falls_back_when_hydration_fails():
    e = gd.event_to_embed(STRIPPED_OPENED, pr_fetcher=lambda r, n: {})
    assert e["title"] == "PR Opened: #42 Untitled PR"       # graceful default...
    assert e["url"] == "https://github.com/robogeosociety/discobots/pull/42"  # ...but still a working link


def test_full_payload_needs_no_fetch():
    def boom(r, n):
        raise AssertionError("should not fetch when payload is already complete")
    e = {"type": "PullRequestEvent", "repo": {"name": "x/y"},
         "payload": {"action": "opened", "number": 1,
                     "pull_request": {"title": "Inline", "user": {"login": "a"},
                                      "html_url": "https://github.com/x/y/pull/1"}}}
    out = gd.event_to_embed(e, pr_fetcher=boom)
    assert out["title"] == "PR Opened: #1 Inline"


def test_closed_unmerged_is_skipped():
    e = {"type": "PullRequestEvent", "repo": {"name": "x/y"},
         "payload": {"action": "closed", "number": 9,
                     "pull_request": {"title": "Nope", "user": {"login": "a"},
                                      "html_url": "u", "merged": False}}}
    assert gd.event_to_embed(e, pr_fetcher=fake_fetcher) is None


# --- the #dev heartbeat additions: issues, releases, CI scan, human-task board ---

from datetime import datetime, timedelta, timezone  # noqa: E402

from discokit.notify import ChangeFeed, StateFile  # noqa: E402

NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)


def _mk_issue_event(action, labels=(), number=7, title="Fix the thing"):
    return {"type": "IssuesEvent", "repo": {"name": "robogeosociety/supervisor"},
            "payload": {"action": action,
                        "issue": {"number": number, "title": title,
                                  "user": {"login": "tommyroar"},
                                  "html_url": f"https://github.com/robogeosociety/supervisor/issues/{number}",
                                  "labels": [{"name": n} for n in labels]}}}


def test_issue_opened_is_announced():
    e = gd.event_to_embed(_mk_issue_event("opened"))
    assert e["title"] == "Issue Opened: #7 Fix the thing"
    assert e["color"] == gd.COLOR_ISSUE


def test_issue_assigned_is_skipped():
    assert gd.event_to_embed(_mk_issue_event("assigned")) is None


def test_human_task_issue_event_is_left_to_the_board_scan():
    assert gd.event_to_embed(_mk_issue_event("opened", labels=("human-task",))) is None


def test_release_published_is_announced():
    e = {"type": "ReleaseEvent", "repo": {"name": "robogeosociety/supervisor"},
         "payload": {"action": "published",
                     "release": {"tag_name": "2026.07.04", "name": "2026.07.04",
                                 "html_url": "https://github.com/robogeosociety/supervisor/releases/tag/2026.07.04"}}}
    out = gd.event_to_embed(e)
    assert out["title"] == "🚀 Release: robogeosociety/supervisor 2026.07.04"
    assert out["color"] == gd.COLOR_RELEASE
    e["payload"]["action"] = "created"
    assert gd.event_to_embed(e) is None  # only `published` is newsworthy


def _ci_fetch(failure_age_days=1):
    created = (NOW - timedelta(days=failure_age_days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fetch(path):
        if path.startswith("/orgs/"):
            return [{"full_name": "robogeosociety/discobots", "name": "discobots",
                     "default_branch": "main", "archived": False,
                     "pushed_at": NOW.strftime("%Y-%m-%dT%H:%M:%SZ")}]
        return {"workflow_runs": [
            {"id": 111, "name": "ci", "conclusion": "failure", "created_at": created,
             "html_url": "https://github.com/robogeosociety/discobots/actions/runs/111"},
            {"id": 112, "name": "ci", "conclusion": "success", "created_at": created},
        ]}
    return fetch


def test_ci_failure_announced_once(tmp_path):
    feed = ChangeFeed(StateFile(tmp_path / "state.json"))
    first = gd.scan_ci_failures(feed, fetch=_ci_fetch(), now=NOW)
    assert [e["title"] for e in first] == ["CI Failed: ci"]
    assert first[0]["color"] == gd.COLOR_CI_FAIL
    # same run again → already seen, nothing new
    assert gd.scan_ci_failures(feed, fetch=_ci_fetch(), now=NOW) == []


def test_ci_old_failures_are_not_replayed(tmp_path):
    feed = ChangeFeed(StateFile(tmp_path / "state.json"))
    stale = gd.scan_ci_failures(feed, fetch=_ci_fetch(failure_age_days=10), now=NOW)
    assert stale == []  # a fresh state volume must not dredge up history


def _board_fetch(items):
    return lambda path: {"items": items}


def _task(number, state="open", comments=0, title="Rack the new disk"):
    return {"number": number, "state": state, "comments": comments, "title": title,
            "repository_url": "https://api.github.com/repos/robogeosociety/robot-geographical-society",
            "html_url": f"https://github.com/robogeosociety/robot-geographical-society/issues/{number}"}


def test_board_first_run_seeds_silently(tmp_path):
    state = StateFile(tmp_path / "tasks.json")
    assert gd.scan_human_tasks(state, fetch=_board_fetch([_task(1), _task(2)])) == []
    # second run, same board → still quiet
    assert gd.scan_human_tasks(state, fetch=_board_fetch([_task(1), _task(2)])) == []


def test_board_transitions_announce_once_each(tmp_path):
    state = StateFile(tmp_path / "tasks.json")
    gd.scan_human_tasks(state, fetch=_board_fetch([_task(1)]))  # seed

    opened = gd.scan_human_tasks(state, fetch=_board_fetch([_task(1), _task(3)]))
    assert [e["title"] for e in opened] == [
        "🧭 Human task opened: robot-geographical-society#3 Rack the new disk"]

    commented = gd.scan_human_tasks(state, fetch=_board_fetch([_task(1, comments=2), _task(3)]))
    assert len(commented) == 1 and commented[0]["title"].startswith("💬 Human task activity")
    assert "**New comments:** 2" in commented[0]["description"]

    closed = gd.scan_human_tasks(state, fetch=_board_fetch([_task(1, state="closed", comments=2), _task(3)]))
    assert len(closed) == 1 and closed[0]["title"].startswith("✅ Human task closed")
    assert closed[0]["color"] == gd.COLOR_TASK_DONE


def test_board_fetch_failure_keeps_snapshot(tmp_path):
    state = StateFile(tmp_path / "tasks.json")
    gd.scan_human_tasks(state, fetch=_board_fetch([_task(1)]))
    assert gd.scan_human_tasks(state, fetch=lambda p: None) == []
    # the snapshot survived — task 1 closing is still detected afterwards
    out = gd.scan_human_tasks(state, fetch=_board_fetch([_task(1, state="closed")]))
    assert len(out) == 1 and out[0]["title"].startswith("✅")
