"""Hermetic tests for `fleet session create` (no network, no real mini).

`ops/fleet.py` normally operates on the mini's ensure-sessions.sh + ~/.claude/channels tree;
the FLEET_ENSURE_FILE / CLAUDE_CHANNELS_DIR env overrides let us point both at a tmp dir. We
reload the module per-test so its module-level path constants pick up that test's tmpdir.

Pins: create appends a DISCORD_PROJECTS row, a duplicate create is rejected, --model adds the
SESSION_MODEL entry (and --effort the SESSION_EFFORT entry), and access.json is written with the
expected new-session keys. Requires `zsh` on PATH (the same `zsh -n` gate production uses).
"""
import importlib
import json
import pathlib
import shutil
import sys
import types

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

# A minimal but structurally-faithful ensure-sessions.sh: the DISCORD_PROJECTS array (one seed
# row + the "add more" comment, exactly like the real file) and the two typeset -A assoc arrays.
SEED_ENSURE = """#!/bin/zsh
DISCORD_PROJECTS=(
  "maps|$HOME/.claude/channels/discord-maps/workspace|$HOME/.claude/channels/discord-maps"
  # add more, e.g.:  "home|/Volumes/dev|$HOME/.claude/channels/discord-home"
)

typeset -A SESSION_MODEL
SESSION_MODEL=( maps claude-sonnet-5 )

typeset -A SESSION_EFFORT
SESSION_EFFORT=( maps high )
"""


@pytest.fixture()
def fleet(tmp_path, monkeypatch):
    """Reload fleet.py with its paths pointed at a fresh tmp roster + channels dir."""
    if not shutil.which("zsh"):
        pytest.skip("zsh not available — fleet uses `zsh -n` to gate roster edits")
    ensure = tmp_path / "ensure-sessions.sh"
    ensure.write_text(SEED_ENSURE)
    channels = tmp_path / "channels"
    channels.mkdir()
    monkeypatch.setenv("FLEET_ENSURE_FILE", str(ensure))
    monkeypatch.setenv("CLAUDE_CHANNELS_DIR", str(channels))
    import fleet as _fleet
    mod = importlib.reload(_fleet)
    mod._TMP_ENSURE = ensure  # noqa: SLF001 — handy handle for assertions
    mod._TMP_CHANNELS = channels
    return mod


def _ns(**kw):
    """argparse.Namespace with create's defaults, overridden by kwargs."""
    base = {"name": "models", "cwd": None, "model": None, "effort": None, "emoji": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_create_appends_roster_row(fleet):
    fleet.cmd_session_create(_ns(name="models"))
    text = fleet._TMP_ENSURE.read_text()
    assert '"models|$HOME/.claude/channels/discord-models/workspace|$HOME/.claude/channels/discord-models"' in text
    # The seed row survives and the new one parses back out of the array.
    names = [p["name"] for p in fleet.projects(text)]
    assert names == ["maps", "models"]
    # The "add more" comment must not have been clobbered.
    assert "# add more, e.g.:" in text


def test_duplicate_create_is_rejected(fleet):
    fleet.cmd_session_create(_ns(name="models"))
    before = fleet._TMP_ENSURE.read_text()
    with pytest.raises(SystemExit) as ei:
        fleet.cmd_session_create(_ns(name="models"))
    assert "already exists" in str(ei.value)
    # Rejected create is a no-op — the roster is byte-identical (no partial write).
    assert fleet._TMP_ENSURE.read_text() == before


def test_model_and_effort_pins_are_added(fleet):
    fleet.cmd_session_create(_ns(name="models", model="claude-opus-5", effort="high"))
    text = fleet._TMP_ENSURE.read_text()
    assert fleet.models(text)["models"] == "claude-opus-5"
    assert fleet.effort(text)["models"] == "high"
    # The pre-existing maps pins are preserved alongside the new one.
    assert fleet.models(text)["maps"] == "claude-sonnet-5"
    assert fleet.effort(text)["maps"] == "high"


def test_no_model_flag_leaves_session_model_untouched(fleet):
    fleet.cmd_session_create(_ns(name="models"))
    text = fleet._TMP_ENSURE.read_text()
    assert "models" not in fleet.models(text)  # unpinned → uses Claude Code's default
    assert "models" not in fleet.effort(text)


def test_access_json_written_with_expected_keys(fleet):
    fleet.cmd_session_create(_ns(name="models", emoji="🤖"))
    access = fleet._TMP_CHANNELS / "discord-models" / "access.json"
    d = json.loads(access.read_text())
    assert set(d) == {"dmPolicy", "allowFrom", "groups", "pending", "mentionPatterns", "ackReaction"}
    assert d["allowFrom"] == [fleet.TOMMY_DISCORD_ID]
    assert d["ackReaction"] == "🤖"
    assert "models" in d["mentionPatterns"] and "modelsbot" in d["mentionPatterns"]
    assert d["dmPolicy"] == "allowlist"


def test_workspace_dir_is_created_mode_700(fleet):
    fleet.cmd_session_create(_ns(name="models"))
    state = fleet._TMP_CHANNELS / "discord-models"
    ws = state / "workspace"
    assert ws.is_dir()
    # Private state tree — 700 on both the state dir and the workspace.
    assert (state.stat().st_mode & 0o777) == 0o700
    assert (ws.stat().st_mode & 0o777) == 0o700


def test_workspace_deployed_from_repo_channel_dir(fleet, tmp_path, monkeypatch):
    # A repo checkout whose channels/models/ has a tracked file and a .gitignored one.
    repo = tmp_path / "repo"
    chan = repo / "channels" / "models"
    chan.mkdir(parents=True)
    (chan / "modelbot.py").write_text("print('hi')\n")
    (chan / ".gitignore").write_text(".env\n")
    (chan / ".env").write_text("SECRET=nope\n")
    monkeypatch.chdir(repo)
    fleet.cmd_session_create(_ns(name="models"))
    ws = fleet._TMP_CHANNELS / "discord-models" / "workspace"
    assert (ws / "modelbot.py").is_file()          # tracked file deployed
    assert not (ws / ".env").exists()              # .gitignore honoured
