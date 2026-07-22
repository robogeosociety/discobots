"""Tests for discokit.botmsg.BotChannel — create-once, edit-thereafter, self-heal."""

import pathlib
import sys
import urllib.error

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from discokit.botmsg import BotChannel  # noqa: E402
from discokit.notify import StateFile  # noqa: E402


class FakeChannel(BotChannel):
    """BotChannel whose _api is scripted instead of hitting Discord."""

    def __init__(self, state, *, patch_raises=None):
        super().__init__("tok", "123", state, ua="test/1.0")
        self.calls = []
        self.patch_raises = patch_raises   # an exception to raise on the first PATCH
        self._next_id = 1000

    def _api(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "PATCH" and self.patch_raises is not None:
            exc, self.patch_raises = self.patch_raises, None
            raise exc
        if method == "POST":
            self._next_id += 1
            return 200, {"id": str(self._next_id)}
        return 200, {}


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, None)


def test_first_upsert_creates_and_persists_id(tmp_path):
    st = StateFile(tmp_path / "state.json")
    bc = FakeChannel(st)
    mid = bc.upsert("hello")
    assert bc.calls == [("POST", "/channels/123/messages")]
    assert mid == "1001"
    assert st.load()["message_id"] == "1001"


def test_second_upsert_edits_in_place(tmp_path):
    st = StateFile(tmp_path / "state.json")
    st.save({"message_id": "555"})
    bc = FakeChannel(st)
    mid = bc.upsert("update")
    assert bc.calls == [("PATCH", "/channels/123/messages/555")]
    assert mid == "555"


def test_deleted_message_404_recreates(tmp_path):
    st = StateFile(tmp_path / "state.json")
    st.save({"message_id": "555"})
    bc = FakeChannel(st, patch_raises=_http_error(404))
    mid = bc.upsert("update")
    # PATCH 404 → falls through to a fresh POST, new id persisted
    assert [m for m, _ in bc.calls] == ["PATCH", "POST"]
    assert mid == "1001"
    assert st.load()["message_id"] == "1001"


def test_non_404_http_error_propagates(tmp_path):
    st = StateFile(tmp_path / "state.json")
    st.save({"message_id": "555"})
    bc = FakeChannel(st, patch_raises=_http_error(500))
    try:
        bc.upsert("update")
    except urllib.error.HTTPError as e:
        assert e.code == 500
    else:
        raise AssertionError("expected the 500 to propagate")
