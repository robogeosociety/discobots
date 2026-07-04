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
