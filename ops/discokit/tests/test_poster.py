# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.poster — the batched notify POST.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_poster.py
"""

from __future__ import annotations

from discokit.poster import EMBEDS_PER_MESSAGE, Poster


class RecordingPoster(Poster):
    """Capture _request(_multipart) calls so callers are testable without httpx."""

    def __init__(self) -> None:
        super().__init__("https://example.invalid/webhook")
        self.calls: list[tuple[str, str, dict]] = []
        self.multipart_calls: list[tuple[str, str, dict, str, bytes]] = []

    def _request(self, method, url, payload):
        self.calls.append((method, url, payload))
        return None

    def _request_multipart(self, method, url, payload, filename, data):
        self.multipart_calls.append((method, url, payload, filename, data))
        return None


def test_post_batches_at_discords_ten_embed_cap():
    poster = RecordingPoster()
    poster.post([{"title": f"e{i}"} for i in range(EMBEDS_PER_MESSAGE + 5)])
    assert [len(p["embeds"]) for _, _, p in poster.calls] == [EMBEDS_PER_MESSAGE, 5]
    assert all(m == "POST" and u == poster.url for m, u, _ in poster.calls)


def test_post_of_nothing_makes_no_requests():
    poster = RecordingPoster()
    poster.post([])
    assert poster.calls == []


def test_dry_post_prints_instead_of_requesting(capsys):
    poster = Poster("https://example.invalid/webhook", dry=True)
    poster.post([{"title": "hello", "color": 0x3FB950}])
    out = capsys.readouterr().out
    assert "hello" in out and "3FB950" in out


def test_create_with_file_routes_through_multipart():
    poster = RecordingPoster()
    poster.create_with_file({"embeds": [{"title": "chart"}]}, "cpu.png", b"\x89PNG...")
    assert poster.calls == []  # never touches the plain-JSON path
    method, url, payload, filename, data = poster.multipart_calls[0]
    assert method == "POST" and url.endswith("?wait=true")
    assert payload == {"embeds": [{"title": "chart"}]}
    assert filename == "cpu.png" and data == b"\x89PNG..."


def test_edit_with_file_routes_through_multipart():
    poster = RecordingPoster()
    poster.edit_with_file(
        "123", {"embeds": [{"title": "chart"}]}, "cpu.png", b"\x89PNG..."
    )
    method, url, _, filename, _ = poster.multipart_calls[0]
    assert method == "PATCH" and url.endswith("/messages/123")
    assert filename == "cpu.png"


def test_dry_create_with_file_prints_size_instead_of_requesting(capsys):
    poster = Poster("https://example.invalid/webhook", dry=True)
    message_id = poster.create_with_file(
        {"embeds": [{"title": "chart"}]}, "cpu.png", b"0123456789"
    )
    out = capsys.readouterr().out
    assert message_id == "dry-0001"
    assert "cpu.png" in out and "10B" in out


def test_request_multipart_declares_the_attachment(monkeypatch):
    """_request_multipart must add the attachments[] entry Discord needs to
    resolve `attachment://<filename>` inside the embed, and hand the raw
    bytes to _send as a files= part (not folded into the JSON body)."""
    poster = Poster("https://example.invalid/webhook")
    captured = {}

    def fake_send(method, url, **kwargs):
        captured.update(kwargs)
        return None

    poster._send = fake_send
    poster._request_multipart("POST", poster.url, {"embeds": [{}]}, "cpu.png", b"data")

    import json

    body = json.loads(captured["data"]["payload_json"])
    assert body["attachments"] == [{"id": 0, "filename": "cpu.png"}]
    assert captured["files"]["files[0]"] == ("cpu.png", b"data", "image/png")
