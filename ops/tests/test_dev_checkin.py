"""Hermetic tests for the daily #dev check-in (no network — fetch injected)."""
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import dev_checkin as dc  # noqa: E402

NOW = datetime(2026, 7, 5, 15, 0, tzinfo=timezone.utc)  # a Sunday


def _fetch(path):
    if "/search/issues" in path and "is%3Apr" in path:
        return {"items": [
            {"number": 40, "title": "Ship the heartbeat",
             "repository_url": "https://api.github.com/repos/robogeosociety/discobots",
             "html_url": "https://github.com/robogeosociety/discobots/pull/40"},
        ]}
    if "/search/issues" in path:
        return {"items": [
            {"number": 156, "title": "Grant cron FDA",
             "repository_url": "https://api.github.com/repos/robogeosociety/robot-geographical-society",
             "html_url": "https://github.com/robogeosociety/robot-geographical-society/issues/156"},
        ]}
    if path.startswith("/orgs/"):
        return [
            {"full_name": "robogeosociety/discobots", "name": "discobots",
             "default_branch": "main", "archived": False, "pushed_at": "2026-07-05T10:00:00Z"},
            {"full_name": "robogeosociety/supervisor", "name": "supervisor",
             "default_branch": "main", "archived": False, "pushed_at": "2026-07-04T10:00:00Z"},
        ]
    if "discobots/actions" in path:
        return {"workflow_runs": [{"conclusion": "success"}]}
    return {"workflow_runs": [{"conclusion": "failure"}]}


def test_next_repo_sync_is_monday_0717_utc():
    nxt = dc.next_repo_sync(NOW)
    assert (nxt.weekday(), nxt.hour, nxt.minute) == (0, 7, 17)
    assert nxt > NOW and (nxt - NOW).days < 7

    # ON a Monday after 07:17 it rolls to the following week
    monday_later = datetime(2026, 7, 6, 8, 0, tzinfo=timezone.utc)
    assert (dc.next_repo_sync(monday_later) - monday_later).days >= 6


def test_checkin_embed_carries_all_four_sections():
    since = datetime(2026, 7, 4, 15, 0, tzinfo=timezone.utc)
    embed = dc.build_checkin(since, now=NOW, fetch=_fetch)
    d = embed["description"]
    assert "Merged since last check-in (1)" in d and "Ship the heartbeat" in d
    assert "CI on main (1/2 green)" in d and "🔴 supervisor" in d
    assert "Open human tasks (1)" in d and "Grant cron FDA" in d
    assert "repo-sync <t:" in d
    assert embed["title"].startswith("☕ Dev check-in")


def test_quiet_day_reads_as_quiet_not_broken():
    def quiet(path):
        if "/search/issues" in path:
            return {"items": []}
        if path.startswith("/orgs/"):
            return []
        return {"workflow_runs": []}
    embed = dc.build_checkin(NOW, now=NOW, fetch=quiet)
    d = embed["description"]
    assert "nothing merged" in d and "queue clear" in d
