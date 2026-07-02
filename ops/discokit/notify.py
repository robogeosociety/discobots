"""discokit.notify — the change-feed layer for the notifier bots.

Every notifier repeats the same two moves: durable JSON state under
~/.local/share/<bot>/, and a "have I announced this id yet?" gate over it.
This module is that copy-paste, once:

    StateFile    tolerant JSON load/save (missing file → {}, mkdir on save)
    ChangeFeed   seen-id set with a size cap, persisted via a StateFile

A bot with a richer state shape (transit's per-alert entries, skills' spotlight
rotation) keeps its own logic and uses StateFile alone; ChangeFeed is the whole
story for the github-style "post each event once" feeds.
"""

from __future__ import annotations

import json
from pathlib import Path


class StateFile:
    """One JSON document on disk. Corrupt or absent reads as {}."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2))


class ChangeFeed:
    """Announce each id exactly once, with the seen-set capped on disk.

        feed = ChangeFeed(StateFile(state_path), cap=500)
        fresh = [e for e in events if feed.is_new(e["id"])]
        ... build + post embeds for fresh ...
        feed.save()
    """

    def __init__(self, state: StateFile, *, cap: int = 500) -> None:
        self.state = state
        self.cap = cap
        self._doc = state.load()
        # A list (not a set) so insertion order survives the round-trip and the
        # cap drops the *oldest* ids.
        self._seen: list[str] = list(self._doc.get("seen_ids", []))
        self._lookup = set(self._seen)

    def is_new(self, item_id: str) -> bool:
        """True exactly once per id; the id is marked seen on first sight."""
        if not item_id or item_id in self._lookup:
            return False
        self._seen.append(item_id)
        self._lookup.add(item_id)
        return True

    def save(self) -> None:
        self._doc["seen_ids"] = self._seen[-self.cap :]
        self.state.save(self._doc)
