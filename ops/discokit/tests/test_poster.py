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
    """Capture _request calls so the batching logic is testable without httpx."""

    def __init__(self) -> None:
        super().__init__("https://example.invalid/webhook")
        self.calls: list[tuple[str, str, dict]] = []

    def _request(self, method, url, payload):
        self.calls.append((method, url, payload))
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
