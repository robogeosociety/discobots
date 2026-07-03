# channels/models — ModelBot workspace

Source of truth for **ModelBot**, the Claude Code channel agent that answers in Discord **#models**.
Its single job is to **read and swap the mini's global base model** by driving the `tommybot model`
CLI (`list` / `use <name> [--save]`) added in
[tommyroar/tommybot#79](https://github.com/tommyroar/tommybot/pull/79). It ships **no tool code of
its own** — unlike MapBot, the whole tool is an already-installed CLI; this workspace is just the
persona that drives it. Pin it to `claude-sonnet-5` (mechanical tool-calling — see the babysitter's
`SESSION_MODEL`, as MapBot pins to sonnet).

This directory deploys to the mini at `~/.claude/channels/discord-models/workspace/` (the agent's
cwd). Secrets + per-channel state (`.env`, `access.json`, `chat_id`) stay on the mini, never here.

## Files

| File | Role |
| --- | --- |
| `CLAUDE.md` | the agent persona (loaded as cwd) — what ModelBot is and which `tommybot model` command to run per request |
| `.gitignore` | excludes the on-mini secrets/state that never belong in the repo |

There is deliberately no code file here: the tool ModelBot drives is `tommybot model`, installed
with the tommybot checkout on the mini.

## External inputs (on the mini, not in this repo)

- **The `tommybot` CLI** — the tommybot checkout at `~/dev/tommybot` (the `tommybot-mini` install).
  ModelBot runs everything as `(cd ~/dev/tommybot && uv run tommybot model …)`, so this workspace has
  no dependency to vendor. The `model` subcommands land there via tommybot#79.
- **`~/dev/tommybot/.env`** — `tommybot model use <name> --save` writes `TOMMYBOT_MODEL="…"` there
  (cwd-relative, so `use` must run from `~/dev/tommybot`). The warm server `com.tommybot.serve-mini`
  sources this `.env` on start (via `scripts/serve.sh`), so it comes up on the saved model. Managed
  *through the CLI*, never edited by hand from here. Read the authoritative persisted value with
  `grep TOMMYBOT_MODEL ~/dev/tommybot/.env`.

## Model swap runbook (on the mini)

A swap is **two steps** — there is no live socket swap, no `--restart` flag, and no `model current`
subcommand:

```sh
# 1. persist the pick to ~/dev/tommybot/.env (cwd-relative → run from the checkout)
(cd ~/dev/tommybot && export PATH="$HOME/.local/bin:$PATH" && uv run tommybot model use <name> --save)
# 2. restart the warm server so it re-sources .env and comes up resident on the new model
launchctl kickstart -k gui/$(id -u)/com.tommybot.serve-mini
```

`<name>` is a model id or unique substring (`8b`, `1.7b`, `4b`, `14b`). Without `--save` the pick is
ephemeral (prints an export hint, does not persist). Step 2 restarts **`com.tommybot.serve-mini`** —
distinct from `bot-mini` (re-queries serve-mini over a socket, no restart) and from
`restart-maclaude` (bounces only the Discord agent session, below). Verify with
`(cd ~/dev/tommybot && uv run tommybot model list)` — the `←` row is the configured/persisted model
(there is no CLI to read the *resident* model, so a restart is required to make a save resident).

## Wiring the channel (one-time, on the mini — off-repo)

Like every channel-session bot, the Discord binding lives on the mini, not in this repo:

1. **Discord app + token** — a new Discord application (bot-per-purpose), its token in
   `~/.claude/channels/discord-models/.env` as `DISCORD_BOT_TOKEN`, and an `access.json`
   (`mentionPatterns` + `ackReaction`, Tommy as sole allowlisted sender).
2. **Session roster row** — in `~/.local/share/claude-channels/ensure-sessions.sh`, add a row to the
   `DISCORD_PROJECTS=( "name|cwd|state_dir" )` array so the launchd babysitter
   (`com.tommydoerr.claude-channels`) spawns and keeps it alive:

   ```sh
   "models|/Users/tommydoerr/.claude/channels/discord-models/workspace|/Users/tommydoerr/.claude/channels/discord-models"
   ```

   The model pin is a **separate `SESSION_MODEL` associative array** in the same file (not an inline
   roster field) — add `models claude-sonnet-5` to it for cheap mechanical tool-calling.
3. **Register it** — add the `models` row to [`AGENT.md`](../../AGENT.md)'s fleet table and the
   ModelBot entry to [`DISCORD.md`](../../DISCORD.md) (done in this PR).

Ongoing edits to an existing channel (cwd, model pin, emoji, aliases) go through
`ops/fleet.py` (`just fleet …`).

## Deploy to the mini

```sh
rsync -az --exclude-from=channels/models/.gitignore channels/models/ \
  tommydoerr@tommys-mac-mini.tail59a169.ts.net:.claude/channels/discord-models/workspace/
# then restart the Discord agent session (NOT the model server):
ssh … 'nomad job dispatch -meta session=models restart-maclaude'
```

`restart-maclaude` bounces **ModelBot's own Discord agent session** so it picks up the new persona —
this is distinct from the model-swap restart (`launchctl kickstart … com.tommybot.serve-mini`, in the
runbook above), which restarts tommybot's warm inference server.
