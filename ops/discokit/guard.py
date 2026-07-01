"""discokit.guard — the private-guild allowlist any Discord-*reading* discobot must honor.

All of Tommy's discobots automation is scoped to ONE private Discord server (guild) —
"tommyroar" (id below). Today's ops/*.py bots (digest, github, transit, watcher, skills,
dashboard, loop, embed) are all one-way *webhooks*: a webhook URL is baked to a single
channel at creation time by Discord itself, so there's no runtime guild-selection for them
to get wrong — this module is defense-in-depth infrastructure for the risk category that
actually matters: anything that *reads* Discord (a bot client, gateway listener, interaction
handler). Unlike a webhook, a bot token can be invited to any server a human adds it to, and
every inbound event it receives carries a `guild_id` — ANY such code in this repo (current or
future) MUST call `is_own_guild()` / `assert_own_guild()` before acting on one.

Prompted by a near-miss: manually clicking through Discord while testing a *different*,
separate bot application wandered into an unrelated public server. Harmless there (read-only
browsing), but exactly the mistake a live event handler must never make on its own.
"""

from __future__ import annotations

import os

# Tommy's private server ("tommyroar" — #ops/#github/#obsidian/#trips/etc.; the ops bots and
# the "Claude Code plugin channels" from DISCORD.md's registry both live here). Env-overridable
# (DISCOBOTS_GUILD_ID) so a fork or test double never has to hardcode this id.
_DEFAULT_GUILD_ID = 1480240435585618064


def own_guild_id() -> int:
    """The one guild id discobots automation is allowed to act in."""
    raw = os.environ.get("DISCOBOTS_GUILD_ID")
    return int(raw) if raw else _DEFAULT_GUILD_ID


def is_own_guild(guild_id: int | str | None) -> bool:
    """True only for Tommy's private server.

    ``None`` (e.g. a DM, which has no guild) is deliberately NOT "own" — DMs sit outside this
    allowlist's scope entirely; a caller that wants to permit DMs decides that separately and
    explicitly, rather than this function silently waving them through.
    """
    if guild_id is None:
        return False
    try:
        return int(guild_id) == own_guild_id()
    except (TypeError, ValueError):
        return False


class ForeignGuildError(RuntimeError):
    """Raised by assert_own_guild — an event/action targeted a guild outside the allowlist."""


def assert_own_guild(guild_id: int | str | None, *, context: str = "") -> None:
    """Raise ForeignGuildError unless ``guild_id`` is Tommy's private server.

    Call this as the first thing any Discord *event-reading* code does with an inbound
    ``guild_id`` — before running a command, posting a reply, or touching any tool.
    """
    if not is_own_guild(guild_id):
        where = f" ({context})" if context else ""
        raise ForeignGuildError(
            f"refusing to act in guild {guild_id!r}{where} — not the allowed guild {own_guild_id()}"
        )
