# discobots

Canonical home for Tommy's Discord bot configs and integration code.

➡️ **The registry of record is [`DISCORD.md`](./DISCORD.md)** — which Discord app/bot
serves which purpose and where each config lives. No secrets are committed here.

## What this is

Three things live here:

1. **The registry** ([`DISCORD.md`](./DISCORD.md)) — the map of every Discord app/bot/webhook
   across Tommy's machines and which repo/`.env` owns each. Tokens stay where their service
   reads them; nothing secret is committed.
2. **The discobots themselves** ([`ops/`](./ops/)) — the notification bots and the live
   `discokit` dashboards, each running as its **own OrbStack container on the always-on Mac
   mini**, built on the mini and **deployed + managed remotely from the MacBook Air**.
3. **MCP servers** ([`mcp/`](./mcp/)) — local (stdio) MCP servers any Claude Code discobot
   channel can load, registered in the mini's `~/.claude.json`. See
   [`DISCORD.md`](./DISCORD.md#mcp-servers-loaded-by-claude-discobot-channels) for the registry
   entry.

## The bots (`ops/`)

| Bot | Schedule | What it does |
| --- | --- | --- |
| **digest** | weekly, Mon 08:15 PT | Weekly ops digest (InfluxDB health + dev-status) → Discord |
| **github** | every 30 min | New GitHub activity for `tommyroar` → Discord |
| **watcher** | daemon | Watches the dev-status server, posts on service up/down changes |
| **transit** | every 5 min | OneBusAway **GTFS-Realtime** alerts for watched routes → transit channel |
| **skills** | every 3 h + daily spotlight | New Claude Code skills the fleet gains → `#skills`, plus a daily 💡 spotlight on an existing one |
| **dashboard** | daemon (30 s poll) | Dynamic **#ops** status board — dev-status readout, one message edited in place |
| **loop** | daemon (60 s poll) | The `obsidian-automations` supervisor loop as a spinning ASCII ferris wheel → **#ops** |
| **embed** | daemon (5 min poll) | tommybot's slow embeddings-sync progress, graphed → **#ops** |

Each is a long-running container (`--restart unless-stopped`): the periodic bots run their
schedule internally via supercronic, the daemons (`watcher`/`dashboard`/`loop`/`embed`) run a
poll loop. Secrets are injected at `docker run` from the host's existing `.env` files — never
baked into an image or this repo. Per-bot details, image layout, and secret sources are in
[`ops/README.md`](./ops/README.md); `loop` and `embed` (the `discokit` dashboards) get a full
tour with real rendered output in [`docs/ops.md`](./docs/ops.md).

## Architecture

```
MacBook Air (control plane)            Mac mini (runtime, always-on)
  discobots/ (git source of truth)         OrbStack engine (auto-starts at login)
  just deploy ── push ─► GitHub ─ pull ─►  /Volumes/dev/discobots ─ docker build ─► images
  just up / ps / logs / down ── ssh ─────► containers: discobot-{digest,github,watcher,transit,skills,dashboard,loop,embed}
```

tommybot (the MLX Obsidian-RAG bot) deliberately stays a bare-metal `launchd` process on the
host (`com.tommybot.bot-mini`, on the mini since 2026-07-01) — MLX needs Apple Metal, which a
Linux container can't reach. Only the network-bound automations are containerized here.

## Operating it (from the Air)

The repo-root [`justfile`](./justfile) is the control plane. Every recipe runs over
SSH/Tailscale — docker executes on the mini, so there's **nothing to install on the Air and
no setup step**:

```sh
just deploy        # git push, then git pull + rebuild images on the mini
just up            # start all eight bots  (just up transit  → just one)
just ps            # list the discobot containers + status
just logs github   # tail a bot's logs     (add -f to follow)
just run-now digest   # fire a periodic bot once now
just dry digest       # fire once in dry-run (no Discord post)
just down          # stop/remove containers
just doctor        # confirm the mini's engine is reachable from the Air
```

## Obsidian link redirector (`/o`)

Discord only makes `http(s)` links clickable — never `obsidian://`. So Obsidian note links
posted to Discord (by the **claudesidian** enrichment bot, and the obsidian-automations daily/
weekly pipelines) go through a tiny **tailnet redirector**:

```
https://tommys-mac-mini.tail59a169.ts.net/o?vault=home&file=Trips%2FLAX%20Summer%20Break
                                              └── same query as obsidian://open ──┘
```

Tapping it opens the page, which bounces the browser to `obsidian://open?…` → the note opens in
Obsidian. **It's not a service** — [`ops/obsidian-redirect.html`](./ops/obsidian-redirect.html)
(a one-line client-side JS redirect) is served **directly by `tailscale serve`**, so there's no
process or container behind it.

Setup (once): `ops/redirect-install.sh` on the mini stages the page to the internal disk and
prints the one-time `sudo tailscale serve --set-path /o …` (serving a file needs root; it
persists across reboots). Tailnet-only, so it works on any of Tommy's Tailscale devices. See
[`ops/README.md`](./ops/README.md#obsidian-link-redirector-o).

## Conventions

- **Secrets never enter this repo.** `.env` files are git-ignored; `ops/run.sh` reads them
  on the mini at `docker run` time. Rotate a secret → `just up <bot>` to recreate.
- **Bot-per-purpose.** Distinct bots are distinct Discord apps with their own tokens — don't
  consolidate them.
- When you add/move a bot, webhook, or integration, **update [`DISCORD.md`](./DISCORD.md)** so
  it stays the registry of record.
