# docs/ops.md — the `ops/` discobots

Eight Discord automations, each its own **OrbStack container on the always-on Mac mini**, built on
the mini and deployed/managed remotely from the MacBook Air via the repo-root
[`justfile`](../justfile). Two are periodic **notifiers** (fire, post, exit); one is a poll
**daemon**; two more are `discokit` **dynamic dashboards** — a daemon that owns ONE Discord message
and PATCH-edits it in place on every poll instead of spamming new posts. Full per-bot secrets/mount
details are in [`ops/README.md`](../ops/README.md); this page is the tour.

## The `discokit` dynamic dashboards

Both post to **#ops** (the #dev heartbeat — the renamed #github channel — is the notifier
`github`'s beat, not a dashboard's; the other webhooks in `observability/grafana/.env` resolve to
#ops/#obsidian/#transit/#weather) and share the same shape: post once, remember the message id, PATCH-edit on every poll, skip
the edit entirely when nothing changed (a content-hash gate), and degrade to the **last-known**
render rather than go silent if their data source is unreachable.

### loop — the supervisor loop as a spinning ASCII ferris wheel

`ops/loop_dashboard.py` draws `obsidiand` (the `obsidian-automations` asyncio + pydoit supervisor
loop) as an 8-cabin wheel, built from its `supervisor_tick` telemetry in the InfluxDB `ops` bucket.
The wheel turns **one cabin per minute**, so the live message visibly spins as the loop ticks — the
lead cabin **◉ is the last tick**, a **✦ cabin is the last event**, both labeled with a relative
timestamp. A footer carries the trigger split (cron / event / backstop — the fire-on-content vs
fire-on-clock ratio) and the doit executed-vs-up-to-date ratio; the header shows lag and
shadow/live mode.

Rendered from real telemetry on the mini:

```text
🎡 obsidiand · the loop          ℹ️ live · lag 0.2s · mem 92% free
        ●
     ●     ●
      ╲ │ ╱
  ✦─────⊙─────◉
      ╱ │ ╲
     ●     ●
        ●
◉ last tick   <t:…:R>              (now)
✦ last event  <t:…:R>  · 8 events/2h   (~9 min ago)
cron 25 · event 8 · backstop 139 · 172 fires/2h · doit ran 0/0
```

Health: **ℹ️ healthy → ⚠️ lag spike** (lag ≥ 5 s) **→ 🔴 stopped** (no `supervisor_tick` in the
window → shows the last-known wheel). Preview locally with no Influx/Discord/deps:

```sh
python3 ops/loop_dashboard.py --dry --demo
```

### embed — tommybot's slow embeddings sync

`ops/embed_dashboard.py` graphs tommybot's embeddings sync — the deliberately **slow, min-RAM
`*/15` trickle** (`tommybot#57`) that replaced an earlier once-daily whole-corpus embed (an OOM
contributor on the 8 GB mini, undeployed 2026-06-29). The trickle session itself emits no
telemetry of its own, so this bot reads tommybot's `embeddings.db` **directly** — a read-only
bind mount of `~/Library/Caches/tommybot`, opened `file:...?mode=ro&immutable=1` (the correct way
to read a live WAL-mode SQLite DB from a read-only mount: it skips the WAL/shm files, reading as
of the last checkpoint, which the trickle folds after every session).

Rendered from the real, in-progress nomic rebuild on the mini:

```text
🐢 tommybot embeddings · the slow sync         ℹ️ 432 chunks embedded
chunks    432 total  (tracking growth from here)
vaults
  camping ███████████   432
  dev     ░░░░░░░░░░░     0
  gear    ░░░░░░░░░░░     0
  home    ░░░░░░░░░░░     0
  travel  ░░░░░░░░░░░     0
last sync <t:…:R> · camping · embedded 48 (changed 48 · rolled 0)
model nomic-ai/nomic-embed-text-v1.5 · wal 16 KB pending checkpoint
```

`camping` fully caught up while the other four vaults sit at zero — exactly the trickle's
alphabetical, changed-first order (`obsidian-automations#136`). As later sessions land, the
**chunks** line grows a self-tracked sparkline (the bot's own polling history — the trickle has no
time-series of its own to draw from) so the slow climb toward full coverage is visible over time.
An untouched vault's empty bar is the visible proof the trickle hasn't reached it yet.

Health: **ℹ️ healthy → ⚠️ no sync in 30 min** (the `*/15` job likely isn't landing) **→ 🔴 DB
unreachable**. Preview locally with no DB/Discord/deps:

```sh
python3 ops/embed_dashboard.py --dry --demo
```

## The rest of the bots

| Bot | Schedule | What it does |
| --- | --- | --- |
| **digest** | weekly, Mon 08:15 PT | Weekly ops digest (InfluxDB health + dev-status) → Discord |
| **github** | every 30 min | New GitHub activity for `tommyroar` → Discord |
| **watcher** | daemon (poll loop) | Watches the dev-status server, posts on service up/down changes |
| **transit** | every 5 min | OneBusAway GTFS-Realtime alerts for watched routes → transit channel |
| **skills** | every 3 h + daily spotlight | New Claude Code skills discobots gains → `#skills`, plus a daily 💡 spotlight on an existing one |
| **dashboard** | daemon (30 s poll) | The original `discokit` dashboard — dev-status readout, one message edited in place, in **#ops** |

## Operating the bots

Every recipe in the repo-root `justfile` runs over SSH/Tailscale — docker executes on the mini,
nothing to install on the Air:

```sh
just deploy          # git push, then git pull + rebuild images on the mini
just up              # start all eight bots   (just up embed  → just one)
just ps               # list the discobot containers + status
just logs loop -f     # tail a bot's logs (follow)
just dry embed        # preview a dashboard bot's render (--dry --demo), no Discord post
just down             # stop/remove containers
```

See [`ops/README.md`](../ops/README.md) for the full per-bot image/mount/secret layout, and
[`AGENT.md`](../AGENT.md) for discobots' agent conventions.
