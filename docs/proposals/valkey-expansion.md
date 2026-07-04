---
type: proposal
implemented_by: []
tracking: 0
---

# Proposal — Valkey beyond telemetry: the fleet's coordination + event backbone

The bus started as one edge (the supervisor tick → the #ops wheel). Its data
structures can do a lot more — but only where they fit the guardrails: Valkey is
for **ephemeral, hot, coordination state**, never the store (that's InfluxDB),
and every use stays **degradable** (a bus outage falls back to today's behaviour,
never wedges a loop). This proposal collects the *less-immediate* ideas and a
scan of the fleet for where they'd land. The two *most* immediate primitives
(distributed lock + windowed counter) ship separately in discobots#34.

> **Not the store.** Locks, counters, dedup sets, retained values, capped streams
> — all fine, because losing them on a restart costs at most a redo. The moment
> you want *history* or *analytical queries*, that's InfluxDB, not Valkey.

## The through-line

Turn the fleet from independent pollers into an **event-driven system**: the bus
is the nervous system (coordination + live), the supervisor is the dispatcher
(cron + bus-event triggers), InfluxDB is the memory, Discord is the face. Replace
external polling/triggers *wherever the source can push*; keep polling only where
it must (a feed with no webhook).

## The ideas (less-immediate)

### 1. Webhook receiver → `fleet.github.event` (kills a 30-min poll) — flagship
`github_discord.py` polls the GitHub events API every 30 min. A tiny HMAC-checked
webhook receiver (the supervisor already hand-rolls HTTP in `ticks_server`) turns
each push/PR/workflow_run into a bus event; the notifier posts **instantly**, and
the *same event* can drive the wiki rebuild and telemetry — one push, many
consumers. Needs an ingress (Tailscale Funnel / a Cloudflare Tunnel); everything
downstream is hand-rolled. *Primitive: pub/sub + a durable stream.*

### 2. Generalize the supervisor's event trigger to bus events
The supervisor's `event`-kind jobs fire only on the vault-mirror edge today.
Let any `fleet.*.event` fire a registered job → the bus + supervisor become a
**self-hosted IFTTT** ("when X, run Y"), replacing external CI/automation
triggers for the local pipelines. *Primitive: pub/sub → the run loop.*

### 3. Event-driven notifiers (drop the polls where the source can push)
`skills_discord.py` polls the filesystem every 3 h; an `fswatch`/`watchdog` →
`fleet.skills.changed` makes it instant. (Transit's GTFS-RT feed has no webhook —
that poll stays.) *Primitive: pub/sub.*

### 4. Work queues between loops
The future gateway enqueues "handle this message," the taste-training loop
consumes reaction events, a render loop consumes "post this." Producers and
consumers stay decoupled and independently restartable. *Primitive: streams /
`LPUSH`+`BRPOP`.*

### 5. Sorted-set delayed scheduling
"Retry at T", cooldown re-arm, "remind me" — a lightweight timer wheel
complementing the supervisor's cron (`ZADD` score=fire-at, `ZRANGEBYSCORE` to
pop due). *Primitive: sorted set.*

### 6. The #149 Claude-router token pool
Concurrent inbound-message tasks sharing one Anthropic client need a shared
per-channel rate-limit/cost counter. *Primitive: the windowed counter (discobots#34).*

### 7. Cross-process locks + shared circuit breaker (the fleet-hosting split)
The supervisor's prohibit-overlap locks and its breaker are in-process today;
when jobs move to their own processes, they must become the distributed lock +
a shared hash. *Primitive: the lock (discobots#34) + a hash.*

## Fleet scan — concrete opportunities

Where these patterns already exist in the code (`file:line` → the fitting primitive):

| # | Site | Today | Valkey fit |
| --- | --- | --- | --- |
| 1 | `discobots/ops/github_discord.py:67` (fetch) + `:168` | polls GitHub API every 30 min; `ChangeFeed(StateFile)` dedup | webhook → `fleet.github.event` (idea 1); dedup set becomes shared `SADD`/`SISMEMBER` |
| 2 | `obsidian-automations/supervisor/context.py:99` | `asyncio.Lock()` per job — **in-process** prohibit-overlap | distributed **lock** once jobs split into processes (idea 7) |
| 3 | `obsidian-automations/supervisor/runners.py:59` | `ctx.state.circuit_open()` — SQLite-persisted breaker, per-process | shared breaker **hash** so all loop processes see one circuit (idea 7) |
| 4 | `obsidian-automations/supervisor/context.py:87` | `repo_changed: asyncio.Event` — the one event trigger | any `fleet.*.event` fires jobs (idea 2) |
| 5 | `discobots/ops/skills_discord.py:49` + fs poll (3 h) | `StateFile` known-skills; filesystem polled on a cron | `fswatch` → `fleet.skills.changed` (idea 3) |
| 6 | `discobots/ops/skills_discord.py:230` | `spotlight_recent` rotation list in a JSON file | shared **list**/sorted-set so rotation is fleet-consistent |
| 7 | `discobots/ops/watcher.py:163` | `down_counter` debounce dict — in-memory, lost on restart | retained **hash** so debounce survives a restart / is shared |
| 8 | `discobots/ops/transit_discord.py:58` | `StateFile` per-alert `first_seen` map | keep as-is (per-bot, poll-only source) — noted for completeness |
| 9 | `tommybot/tommybot/bot.py:60` | `TOMMYBOT_LIVE_FILE` telemetry heartbeat (a file) | bridge → `fleet.tommybot.telemetry` so the chat panel subscribes not polls |
| 10 | `discobots/ops/chat_dashboard.py` (5 s poll of the live file) | polls the file | consume idea-9's bus topic instead |
| 11 | `obsidian-automations/supervisor/runners.py:5` | `run_cron: while True: sleep-until-next` | keep cron; **add** bus-event firing beside it (idea 2) |
| 12 | future #149 gateway | per-channel API budget (not yet built) | windowed **counter** token pool (idea 6) |

## Rollout

Each idea is an independent PR, degradable, and reversible. Suggested order by
leverage: **1** (webhook, kills a poll + proves webhook→bus→multi-consumer),
then **2** (generalized event trigger, the multiplier), then **9/10** (tommybot
telemetry bridge), then **7** as the fleet-hosting split lands. `implemented_by`
tracks the PRs as they open.
