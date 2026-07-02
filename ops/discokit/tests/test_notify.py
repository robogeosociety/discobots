# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.notify — StateFile + ChangeFeed.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_notify.py
"""

from __future__ import annotations

from discokit.notify import ChangeFeed, StateFile


def test_statefile_missing_file_loads_as_empty(tmp_path):
    assert StateFile(tmp_path / "nope" / "state.json").load() == {}


def test_statefile_corrupt_file_loads_as_empty(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json")
    assert StateFile(p).load() == {}


def test_statefile_roundtrip_creates_parent_dirs(tmp_path):
    sf = StateFile(tmp_path / "a" / "b" / "state.json")
    sf.save({"k": [1, 2]})
    assert sf.load() == {"k": [1, 2]}


def test_changefeed_announces_each_id_exactly_once(tmp_path):
    feed = ChangeFeed(StateFile(tmp_path / "state.json"))
    assert feed.is_new("e1") is True
    assert feed.is_new("e1") is False
    assert feed.is_new("") is False  # empty ids are never "new"


def test_changefeed_survives_a_restart(tmp_path):
    sf = StateFile(tmp_path / "state.json")
    feed = ChangeFeed(sf)
    assert feed.is_new("e1") is True
    feed.save()
    assert ChangeFeed(sf).is_new("e1") is False


def test_changefeed_cap_drops_the_oldest_ids(tmp_path):
    sf = StateFile(tmp_path / "state.json")
    feed = ChangeFeed(sf, cap=3)
    for i in range(5):
        assert feed.is_new(f"e{i}")
    feed.save()
    reloaded = ChangeFeed(sf, cap=3)
    assert reloaded.is_new("e0") is True  # oldest fell off the cap
    assert reloaded.is_new("e4") is False  # newest survived


def test_changefeed_preserves_other_state_keys(tmp_path):
    sf = StateFile(tmp_path / "state.json")
    sf.save({"other": 42})
    feed = ChangeFeed(sf)
    feed.is_new("e1")
    feed.save()
    assert sf.load() == {"other": 42, "seen_ids": ["e1"]}
