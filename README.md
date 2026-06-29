# discobots

Canonical home for Tommy's Discord bot configuration and integration code on `~/dev`
(`/Volumes/dev/discobots`). This repo is the **registry + source of truth** for *which
Discord app/bot serves which purpose and where its config lives* — not a single secrets
vault. Tokens stay where their service reads them (each bot is a distinct Discord
application; bot-per-purpose is intentional). **No `.env` / tokens / webhook URLs are ever
committed here** — see `.gitignore`.

## Bot / app registry

### Bot tokens (one Discord app per purpose)

| Bot / app | Token location | Key(s) | Purpose | Repo |
| --- | --- | --- | --- | --- |
| **tommybot** | `~/dev/tommybot/.env` | `DISCORD_BOT_TOKEN`, `DISCORD_CHANNELS` | Main Obsidian-RAG bot (MLX RAG over vault + InfluxDB); Nomad service `tommybot`. Also the **fallback token** for obsidian-automations. | `tommyroar/tommybot` |
| **ask-dash** | `~/dev/observability/ask-dash/.env` | `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_ALLOWED_USER_IDS` | `/ask` slash-command gateway to the observability stack. | `tommyroar/observability-config` |
| **Claude Code plugin channels** | `~/.claude/channels/discord-*/.env` | `DISCORD_BOT_TOKEN` (+ `access.json`) | Claude Code agent chat channels — `discord`, `-dev`, `-devchan`, `-ops`, `-home`, `-camp`, `-obsidian`, `-trips`. **Each channel is its own bot/app, plugin-managed.** Do not relocate. | n/a (system-managed) |

### Webhooks (centralized)

| Location | Key(s) | Purpose | Repo |
| --- | --- | --- | --- |
| `~/dev/observability/grafana/.env` | `DISCORD_WEBHOOK_URL`, `DISCORD_WEBHOOK_TRANSIT`, `DISCORD_WEBHOOK_DIGEST`, `DISCORD_WEBHOOK_WEATHER`, `DISCORD_WEBHOOK_URL_*` | **Single source of truth** for all notification/alert webhooks. Deployed to `~/.observability/grafana/.env` for the container. | `tommyroar/observability-config` |

## Consumers (read the configs above)

| Code | Reads | Repo |
| --- | --- | --- |
| `~/dev/discord-ops/` — `digest.py`, `github_discord.py`, `transit_discord.py`, `watcher.py`, `discord-mountain-notify.ts`, `discord-campsite-notify.ts` + `nomad/` jobs (discord-digest, discord-github, discord-transit) | webhooks from `observability/grafana/.env` | **none yet** — candidate to migrate here |
| `~/dev/obsidian-automations/automations/discord_notify.py`, `enrichment_discord.py` | `DISCORD_BOT_TOKEN` (falls back to `tommybot/.env`), `DISCORD_WEBHOOK_URL*` | `tommyroar/obsidian-automations` |

## Conventions

- **Bot-per-purpose.** Don't consolidate distinct bots' tokens into one file — they are
  separate Discord applications.
- **Webhooks live in `observability/grafana/.env`.** Add new webhook URLs there, then
  record them in the table above.
- **Secrets never enter this repo.** Store an `.env.example` (placeholder keys only) if a
  bot moves its code here; the real `.env` is git-ignored.
- When you add/move a Discord bot, webhook, or integration, **update this README** so it
  stays the registry of record.

## Open items

- [ ] Migrate `~/dev/discord-ops/` into this repo (it's currently unversioned). Touches
      Nomad job spec paths (`discord-ops/nomad/*`) — do as a deliberate, separate change.
