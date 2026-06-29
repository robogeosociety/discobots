# discobots

Canonical home for Tommy's Discord bot configs and integration code.

➡️ **The registry of record is [`DISCORD.md`](./DISCORD.md)** — which Discord app/bot
serves which purpose and where each config lives. No secrets are committed here.

## What this is

Two things live here:

1. **The registry** ([`DISCORD.md`](./DISCORD.md)) — the map of every Discord app/bot/webhook
   across Tommy's machines and which repo/`.env` owns each. Tokens stay where their service
   reads them; nothing secret is committed.
2. **The discobots themselves** ([`ops/`](./ops/)) — the notification/automation bots, each
   running as its **own OrbStack container on the always-on Mac mini**, built on the mini and
   **deployed + managed remotely from the MacBook Air**.

## The bots (`ops/`)

| Bot | Schedule | What it does |
| --- | --- | --- |
| **digest** | weekly, Mon 08:15 PT | Weekly ops digest (InfluxDB health + dev-status) → Discord |
| **github** | every 30 min | New GitHub activity for `tommyroar` → Discord |
| **watcher** | daemon | Watches the dev-status server, posts on service up/down changes |
| **transit** | every 5 min | OneBusAway **GTFS-Realtime** alerts for watched routes → transit channel |

Each is a long-running container (`--restart unless-stopped`): the periodic bots run their
schedule internally via supercronic, watcher runs a poll loop. Secrets are injected at
`docker run` from the host's existing `.env` files — never baked into an image or this repo.
Per-bot details, image layout, and the secret sources are in [`ops/README.md`](./ops/README.md).

## Architecture

```
MacBook Air (control plane)            Mac mini (runtime, always-on)
  discobots/ (git source of truth)         OrbStack engine (auto-starts at login)
  just deploy ── push ─► GitHub ─ pull ─►  /Volumes/dev/discobots ─ docker build ─► images
  just up / ps / logs / down ── ssh ─────► containers: discobot-{digest,github,watcher,transit}
```

tommybot (the MLX Obsidian-RAG bot) deliberately stays a `raw_exec` Nomad job on the host —
MLX needs Apple Metal, which a Linux container can't reach. Only the network-bound
automations are containerized here.

## Operating it (from the Air)

The repo-root [`justfile`](./justfile) is the control plane. Every recipe runs over
SSH/Tailscale — docker executes on the mini, so there's **nothing to install on the Air and
no setup step**:

```sh
just deploy        # git push, then git pull + rebuild images on the mini
just up            # start all four bots   (just up transit  → just one)
just ps            # list the discobot containers + status
just logs github   # tail a bot's logs     (add -f to follow)
just run-now digest   # fire a periodic bot once now
just dry digest       # fire once in dry-run (no Discord post)
just down          # stop/remove containers
just doctor        # confirm the mini's engine is reachable from the Air
```

## Conventions

- **Secrets never enter this repo.** `.env` files are git-ignored; `ops/run.sh` reads them
  on the mini at `docker run` time. Rotate a secret → `just up <bot>` to recreate.
- **Bot-per-purpose.** Distinct bots are distinct Discord apps with their own tokens — don't
  consolidate them.
- When you add/move a bot, webhook, or integration, **update [`DISCORD.md`](./DISCORD.md)** so
  it stays the registry of record.
