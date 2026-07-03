"""discokit.dashboard — one message, edited in place. The "dynamic dashboard".

Instead of re-posting a new message each cycle (chat spam), a Dashboard posts
ONCE, remembers the message id in durable state, and PATCH-edits that same
message on every tick — but only when the *content actually changed* (hash diff).

Freshness is free: the appended `updated <t:EPOCH:R>` renders "3 minutes ago" and
self-updates on every client with zero API calls, so between real changes we make
no requests at all. EPOCH is the last time the content changed — honest, not a
fake "just now" on every poll.

State (message id + content signature + changed-at) is one JSON file keyed by the
dashboard name, so the message survives container restarts. On a 404 (a human
deleted it) the dashboard re-posts and re-persists.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

from .poster import Poster


class Dashboard:
    def __init__(
        self,
        poster: Poster,
        *,
        state_path: str | Path,
        key: str,
        source: str = "discokit",
    ) -> None:
        self.poster = poster
        self.state_path = Path(state_path)
        self.key = key
        self.source = source
        self.state = self._load()

    # --- state --------------------------------------------------------------
    def _load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=2))

    def _slot(self) -> dict:
        return self.state.setdefault(self.key, {})

    # --- one tick -----------------------------------------------------------
    def tick(self, payload: dict, *, image: tuple[str, bytes] | None = None) -> str:
        """Reconcile the live payload (and optional chart image) with Discord.

        Returns one of: "created" | "edited" | "unchanged".
        `payload` is a webhook body ({"embeds": [...]}) WITHOUT the freshness
        stamp — the dashboard owns that so it only bumps on real change.
        `image`, if given, is (filename, png_bytes) — e.g. from
        `discokit.chart` — and the caller's embed must already reference it:
        `embeds[0]["image"] = {"url": f"attachment://{filename}"}`. Included
        in the change signature, so a redrawn chart with identical embed text
        still counts as a change.
        """
        slot = self._slot()
        signature = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
            + (image[1] if image else b"")
        ).hexdigest()
        now = int(time.time())
        changed = signature != slot.get("sig")
        if changed:
            slot["sig"] = signature
            slot["changed_at"] = now

        message_id = slot.get("message_id")
        if message_id and not changed:
            self._save()
            return "unchanged"

        stamped = self._stamp(payload, slot.get("changed_at", now))
        if not message_id:
            slot["message_id"] = (
                self.poster.create_with_file(stamped, *image)
                if image
                else self.poster.create(stamped)
            )
            self._save()
            return "created"

        alive = (
            self.poster.edit_with_file(message_id, stamped, *image)
            if image
            else self.poster.edit(message_id, stamped)
        )
        if not alive:  # 404 — re-post and re-persist a fresh id
            slot["message_id"] = (
                self.poster.create_with_file(stamped, *image)
                if image
                else self.poster.create(stamped)
            )
        self._save()
        return "edited"

    def _stamp(self, payload: dict, epoch: int) -> dict:
        out = copy.deepcopy(payload)
        embeds = out.get("embeds") or []
        if embeds:
            desc = embeds[0].get("description", "") or ""
            stamp = f"\n\n_updated <t:{epoch}:R> · {self.source}_"
            embeds[0]["description"] = (desc + stamp).strip()
        return out
