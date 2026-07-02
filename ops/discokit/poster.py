"""discokit.poster — the Discord webhook transport.

Three calls cover the fleet:
    post(embeds)                    fire-and-forget notify, batched ≤10/message
    create(payload) -> message_id   POST ?wait=true (so we get the id back)
    edit(message_id, payload)       PATCH .../messages/<id>  (edit in place)

Handles the per-webhook 429 bucket (5 req / 2 s) with a Retry-After back-off,
and treats a 404 on edit as "the message was deleted" so the dashboard can
re-post. httpx is imported lazily so --dry runs need no dependency at all.
"""

from __future__ import annotations

import sys
import time

EMBEDS_PER_MESSAGE = 10  # Discord's hard cap per webhook execute


class Poster:
    def __init__(self, webhook_url: str | None, *, dry: bool = False) -> None:
        self.url = webhook_url
        self.dry = dry

    # --- public API ---------------------------------------------------------
    def post(self, embeds: list[dict]) -> None:
        """POST embeds as ordinary webhook messages, batching to Discord's cap."""
        for i in range(0, len(embeds), EMBEDS_PER_MESSAGE):
            batch = embeds[i : i + EMBEDS_PER_MESSAGE]
            if self.dry:
                print(f"  ┌─ POST    {len(batch)} embed(s)")
                self._preview({"embeds": batch})
                continue
            self._request("POST", self.url, {"embeds": batch})

    def create(self, payload: dict) -> str | None:
        """POST with ?wait=true and return the new message id (or None)."""
        if self.dry:
            print("  ┌─ CREATE  POST ?wait=true")
            self._preview(payload)
            return "dry-0001"
        resp = self._request("POST", f"{self.url}?wait=true", payload)
        if resp is None:
            return None
        try:
            return resp.json().get("id")
        except Exception:
            return None

    def edit(self, message_id: str, payload: dict) -> bool:
        """PATCH the message in place. Returns False if it's gone (404)."""
        if self.dry:
            print(f"  ├─ EDIT    PATCH …/messages/{message_id}")
            self._preview(payload)
            return True
        resp = self._request("PATCH", f"{self.url}/messages/{message_id}", payload)
        if resp is not None and resp.status_code == 404:
            return False
        return True

    # --- internals ----------------------------------------------------------
    def _request(self, method: str, url: str, payload: dict):
        import httpx

        for _ in range(3):
            try:
                resp = httpx.request(method, url, json=payload, timeout=15)
            except Exception as exc:  # noqa: BLE001 — network hiccup shouldn't crash the loop
                print(f"[poster] {method} failed: {exc}", file=sys.stderr)
                return None
            if resp.status_code == 429:
                retry = resp.headers.get("retry-after")
                if retry is None:
                    try:
                        retry = resp.json().get("retry_after", 1)
                    except Exception:
                        retry = 1
                time.sleep(float(retry))
                continue
            if resp.status_code >= 400 and resp.status_code != 404:
                print(
                    f"[poster] {method} {resp.status_code}: {resp.text[:200]}",
                    file=sys.stderr,
                )
            return resp
        return None

    def _preview(self, payload: dict) -> None:
        for embed in payload.get("embeds", []):
            title = embed.get("title", "")
            print(f"  │   «{title}»  color=#{embed.get('color', 0):06X}")
            desc = embed.get("description", "") or ""
            for line in desc.splitlines():
                print(f"  │     {line}")
