# channels/models — ModelBot workspace

Source of truth for **ModelBot**, the Claude Code channel agent that answers in Discord **#models**.
Its single job is to **read and hot-swap the mini's global base model** by driving the `tommybot
model` CLI (`list` / `use <name> [--restart]` / `current`) added in
[tommyroar/tommybot#79](https://github.com/tommyroar/tommybot/pull/79). It ships **no tool code of
its own** — unlike MapBot, the whole tool is an already-installed CLI; this workspace is just the
persona that drives it. Pin it to a cheap model (mechanical tool-calling — see the babysitter's
`SESSION_MODEL`, as MapBot pins to sonnet).

This directory deploys to the mini at `~/.claude/channels/discord-models/workspace/` (the agent's
cwd). Secrets + per-channel state (`.env`, `access.json`, `chat_id`) stay on the mini, never here.

## Files

| File | Role |
| --- | --- |
| `CLAUDE.md` | the agent persona (loaded as cwd) — what ModelBot is and which `tommybot model` subcommand to run per request |
| `.gitignore` | excludes the on-mini secrets/state that never belong in the repo |

There is deliberately no code file here: the tool ModelBot drives is `tommybot model`, installed
with the tommybot checkout on the mini.

## External inputs (on the mini, not in this repo)

- **The `tommybot` CLI** — the tommybot checkout at `~/dev/tommybot` (the `tommybot-mini` install).
  ModelBot runs everything as `(cd ~/dev/tommybot && uv run tommybot model …)`, so this workspace has
  no dependency to vendor. The `model` subcommands land there via tommybot#79.
- **The global config TOML** — `tommybot model use` writes `model = "…"` there (and sets
  `TOMMYBOT_MODEL`); the warm server reads it at (re)start. Managed *through the CLI*, never edited by
  hand from here.

## Wiring the channel (one-time, on the mini — off-repo)

Like every channel-session bot, the Discord binding lives on the mini, not in this repo:

1. **Discord app + token** — a new Discord application (bot-per-purpose), its token in
   `~/.claude/channels/discord-models/.env` as `DISCORD_BOT_TOKEN`, and an `access.json`
   (`mentionPatterns` + `ackReaction`, Tommy as sole allowlisted sender).
2. **Session roster row** — add a `DISCORD_PROJECTS` entry (`name|cwd|state`) for `models` to
   `~/.local/share/claude-channels/ensure-sessions.sh` so the launchd babysitter
   (`com.tommydoerr.claude-channels`) spawns and keeps it alive; optionally pin `SESSION_MODEL` to a
   cheap model.
3. **Register it** — add the `models` row to [`AGENT.md`](../../AGENT.md)'s fleet table and the
   ModelBot entry to [`DISCORD.md`](../../DISCORD.md) (done in this PR).

Ongoing edits to an existing channel (cwd, model pin, emoji, aliases) go through
`ops/fleet.py` (`just fleet …`).

## Deploy to the mini

```sh
rsync -az --exclude-from=channels/models/.gitignore channels/models/ \
  tommydoerr@tommys-mac-mini.tail59a169.ts.net:.claude/channels/discord-models/workspace/
# then restart the session:
ssh … 'nomad job dispatch -meta session=models restart-maclaude'
```
