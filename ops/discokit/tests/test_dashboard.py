# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.dashboard — the edit-in-place upsert, focused on
the image-aware tick() path added alongside discokit.chart.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_dashboard.py
"""

from __future__ import annotations

from discokit.dashboard import Dashboard


class FakePoster:
    """Records which method Dashboard called, without touching Discord."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._next_id = 1

    def create(self, payload):
        self.calls.append(("create", payload))
        mid = f"msg-{self._next_id}"
        self._next_id += 1
        return mid

    def edit(self, message_id, payload):
        self.calls.append(("edit", message_id, payload))
        return True

    def create_with_file(self, payload, filename, data):
        self.calls.append(("create_with_file", payload, filename, data))
        mid = f"msg-{self._next_id}"
        self._next_id += 1
        return mid

    def edit_with_file(self, message_id, payload, filename, data):
        self.calls.append(("edit_with_file", message_id, payload, filename, data))
        return True


def _dashboard(tmp_path, poster=None) -> Dashboard:
    return Dashboard(
        poster or FakePoster(), state_path=tmp_path / "state.json", key="d"
    )


def test_first_tick_with_image_creates_with_file(tmp_path):
    poster = FakePoster()
    dash = _dashboard(tmp_path, poster)
    dash.tick({"embeds": [{"description": "x"}]}, image=("cpu.png", b"png-bytes"))
    assert poster.calls[0][0] == "create_with_file"
    assert poster.calls[0][2:] == ("cpu.png", b"png-bytes")


def test_unchanged_image_makes_no_second_call(tmp_path):
    poster = FakePoster()
    dash = _dashboard(tmp_path, poster)
    payload = {"embeds": [{"description": "x"}]}
    dash.tick(payload, image=("cpu.png", b"same-bytes"))
    dash.tick(payload, image=("cpu.png", b"same-bytes"))
    assert (
        len(poster.calls) == 1
    )  # second tick is a no-op: identical text + identical bytes


def test_same_text_different_image_bytes_still_edits(tmp_path):
    poster = FakePoster()
    dash = _dashboard(tmp_path, poster)
    payload = {"embeds": [{"description": "x"}]}
    dash.tick(payload, image=("cpu.png", b"frame-1"))
    result = dash.tick(payload, image=("cpu.png", b"frame-2"))
    assert result == "edited"
    assert poster.calls[-1][0] == "edit_with_file"
    assert poster.calls[-1][-1] == b"frame-2"


def test_tick_without_image_is_unaffected(tmp_path):
    """Existing text-only dashboards must see zero behavior change."""
    poster = FakePoster()
    dash = _dashboard(tmp_path, poster)
    dash.tick({"embeds": [{"description": "x"}]})
    assert poster.calls[0][0] == "create"


def test_404_on_edit_with_file_reposts(tmp_path):
    class GoneOnceAtEdit(FakePoster):
        def edit_with_file(self, message_id, payload, filename, data):
            self.calls.append(("edit_with_file", message_id, payload, filename, data))
            return False  # message was deleted

    poster = GoneOnceAtEdit()
    dash = _dashboard(tmp_path, poster)
    dash.tick({"embeds": [{"description": "x"}]}, image=("cpu.png", b"frame-1"))
    dash.tick({"embeds": [{"description": "x"}]}, image=("cpu.png", b"frame-2"))
    assert [c[0] for c in poster.calls] == [
        "create_with_file",
        "edit_with_file",
        "create_with_file",
    ]
