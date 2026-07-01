# DISCORD.md — Discord bot config registry

Canonical home for Tommy's Discord bot configuration and integration code on `~/dev`
(`/Volumes/dev/discobots`, repo `tommyroar/discobots`). This file is the **registry +
source of truth** for *which
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
| `~/dev/observability/grafana/.env` | `DISCORD_WEBHOOK_URL`, `DISCORD_WEBHOOK_TRANSIT`, `DISCORD_WEBHOOK_DIGEST`, `DISCORD_WEBHOOK_WEATHER`, `DISCORD_WEBHOOK_SKILLS`, `DISCORD_WEBHOOK_OPS`, `DISCORD_WEBHOOK_URL_*` | **Single source of truth** for all notification/alert webhooks. Deployed to `~/.observability/grafana/.env` for the container. `DISCORD_WEBHOOK_OPS` is the #ops webhook (the `dashboard` and `loop` bots use it, falling back to the general `DISCORD_WEBHOOK_URL` → #ops). | `tommyroar/observability-config` |

## Consumers (read the configs above)

| Code | Reads | Repo |
| --- | --- | --- |
| **`ops/`** (this repo) — `digest.py`, `github_discord.py`, `transit_discord.py`, `watcher.py`, `skills_discord.py`. **Now containerized** (OrbStack), one container per bot, managed from the Air. See [`ops/README.md`](./ops/README.md). | webhooks from `observability/grafana/.env` (skills uses `DISCORD_WEBHOOK_SKILLS`); digest also reads `ask-dash/.env` InfluxDB creds; github uses `gh auth token`; skills reads host `~/.claude/{skills,plugins}` | `tommyroar/discobots` |
| `~/dev/obsidian-automations/automations/discord_notify.py`, `enrichment_discord.py` | `DISCORD_BOT_TOKEN` (falls back to `tommybot/.env`), `DISCORD_WEBHOOK_URL*` | `tommyroar/obsidian-automations` |

## Where the discobots run (OrbStack on the mini, managed from the Air)

The `ops/` automations run as **individual OrbStack containers on the always-on Mac mini**,
built on the mini and controlled remotely from the MacBook Air. tommybot stays a `raw_exec`
Nomad job on the host (MLX needs Apple Metal — no GPU in a Linux container).

- **Control plane:** the repo-root [`justfile`](./justfile) on the Air. `just deploy` (push +
  `git pull` + build on the mini) → `just up` / `down` / `ps` / `logs` / `run-now`. Every
  recipe runs over SSH/Tailscale (docker executes on the mini with OrbStack's bin on PATH) —
  no docker client on the Air, no change to the mini's shell profile, no setup step.
- **Secrets stay on the mini host** and are injected at `docker run` by `ops/run.sh` (read
  from `observability/{grafana,ask-dash}/.env`, `gh auth token`, transit's `service.yaml`).
  Nothing secret enters an image or this repo.
- **Bots:** `digest` (weekly Mon 08:15), `github` (every 30 min), `watcher` (daemon),
  `transit` (every 5 min — OneBusAway GTFS-RT alerts for watched routes → transit channel).

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

- [x] ~~Migrate `~/dev/discord-ops/` into this repo~~ — **done**: vendored into `ops/` and
      re-architected as OrbStack containers (see above). Old Nomad `raw_exec` jobs
      (`discord-digest`, `discord-github`) are retired at cutover; the original
      `/Volumes/dev/discord-ops` is kept as `.discord-ops.bak` until the containers are proven.
- [x] ~~The `.ts` notifiers have no trigger~~ — **resolved**: they were misfiled Cloudflare
      **Worker** modules, not mini bots. The canonical mountain notifier lives in
      `is-the-mountain-out/worker/src/discord-mountain-notify.ts` (fires from that Worker's
      `scheduled()` handler); campsite notifications live in
      `robot-geographical-society/backend/src/discord.ts`. Stale copies removed from this repo.
