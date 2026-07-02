# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.guard — the private-guild allowlist.

Run (from ops/, so `discokit` resolves as a package — plain `pytest` won't put ops/ on
sys.path, `python -m pytest` does):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_guard.py
"""

from __future__ import annotations

import pytest

from discokit import guard

_OWN = 1480240435585618064
_OTHER = 999999999999999999


def test_own_guild_id_defaults_to_the_private_server():
    assert guard.own_guild_id() == _OWN


def test_own_guild_id_respects_env_override(monkeypatch):
    monkeypatch.setenv("DISCOBOTS_GUILD_ID", "42")
    assert guard.own_guild_id() == 42


def test_is_own_guild_true_for_the_allowed_id():
    assert guard.is_own_guild(_OWN) is True
    assert guard.is_own_guild(str(_OWN)) is True  # discord.py ids often arrive as str


def test_is_own_guild_false_for_anything_else():
    assert guard.is_own_guild(_OTHER) is False
    assert guard.is_own_guild("not-a-guild-id") is False


def test_is_own_guild_false_for_none_a_dm_has_no_guild():
    assert guard.is_own_guild(None) is False


def test_assert_own_guild_passes_silently_for_the_allowed_id():
    guard.assert_own_guild(_OWN)  # no exception


def test_assert_own_guild_raises_for_a_foreign_guild():
    with pytest.raises(guard.ForeignGuildError, match=str(_OTHER)):
        guard.assert_own_guild(_OTHER, context="on_message")


def test_assert_own_guild_raises_for_a_dm():
    with pytest.raises(guard.ForeignGuildError):
        guard.assert_own_guild(None, context="dm handler")
