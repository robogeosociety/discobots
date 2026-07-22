"""discokit.botmsg — bot-token edit-in-place for a single Discord message.

The dashboards (minimem / orbmem / claude-heatmap) each keep ONE message in a
channel and PATCH it every tick — authored by a bot (not a webhook) so the same
bot can edit it. This is that transport, shared:

    bc = BotChannel(token, channel_id, StateFile(state_path), ua="discord-mini-mem/1.0")
    bc.upsert(content)   # creates the message the first time, edits it thereafter

The message id persists in the StateFile (`discokit.notify`), so a restart re-uses
the same message instead of posting a duplicate. If the message was deleted
out from under us (404 on edit), `upsert` transparently re-creates it.

Webhook posting stays in `discokit.poster`; this is its bot-token sibling for the
edit-one-message dashboards, which webhooks can't do (a webhook can only edit
messages it authored).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .notify import StateFile

API = "https://discord.com/api/v10"


class BotChannel:
    def __init__(
        self,
        token: str,
        channel_id: str,
        state: StateFile,
        *,
        ua: str = "discobots/1.0",
        timeout: int = 20,
    ) -> None:
        self.token = token
        self.channel = channel_id
        self.state = state
        self.ua = ua
        self.timeout = timeout

    def _api(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            API + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bot {self.token}",
                "Content-Type": "application/json",
                "User-Agent": self.ua,
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status, (json.loads(r.read()) if r.status not in (204,) else {})

    def upsert(self, content: str) -> str:
        """Edit the tracked message with `content`, creating it if none exists yet.

        Returns the message id. A 404 on edit (message deleted) falls through to a
        fresh create, so the dashboard self-heals instead of editing a ghost forever.
        """
        doc = self.state.load()
        mid = doc.get("message_id")
        if mid:
            try:
                self._api("PATCH", f"/channels/{self.channel}/messages/{mid}", {"content": content})
                return mid
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                # message was deleted — drop the stale id and re-create below
        _, msg = self._api("POST", f"/channels/{self.channel}/messages", {"content": content})
        doc["message_id"] = msg["id"]
        self.state.save(doc)
        return msg["id"]
