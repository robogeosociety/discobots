# BUS.md — the fleet message bus contract

The connective tissue between the fleet's **separate loops** — the
obsidian-automations **supervisor** (level-1 loop of loops), **discobot-live**
(the discokit inner loop), **tommybot** on the Air, and the future **gateway**.
Like tommybot's `docs/live.md`, this is a *contract*, not a shared library:
each repo implements it against the same transport. The reference client is
[`ops/discokit/bus.py`](../ops/discokit/bus.py).

## Why a bus, and the one rule

The loops are deliberately fault-isolated — each its own process, supervised but
not sharing an address space. The bus lets them coordinate **without calling
into each other**: publish is fire-and-forget, consumers read independently.

> **The bus is an accelerant, never a dependency.** Every publish is
> fire-and-forget (bus down → log and continue, never block a tick), and every
> consumer keeps its direct-poll fallback (the ferris wheel still reads InfluxDB,
> the chat panel still reads the live file). A bus outage must degrade, not
> cascade — otherwise it becomes the single point of failure that re-couples the
> very fault domains the separate loops exist to isolate.

## Transport

**Valkey** (Redis-compatible, BSD-3) — one service covers all three needs:
`PUBLISH/SUBSCRIBE` (telemetry fan-out), `SET … EX` (retained last-value), and
Streams + consumer groups (durable events with replay). It runs as one
supervised `service` — on the mini today (`discobot-valkey`, loopback-only), and
it belongs in the fleet supervisor's `REGISTRY` when that lands (supervision
integrates upward; execution stays each loop's own process).

- **Connection:** `BUS_URL` (or `DISCOBOTS_BUS_URL`), e.g.
  `redis://host.docker.internal:6379` for a mini container, or
  `redis://<tailscale-ip>:6379` from the Air. Unset ⇒ the bus is disabled and
  everything degrades to direct polling.
- **Privacy:** bound to loopback on the mini for now; tailnet exposure (for
  tommybot on the Air) is a documented follow-on, never a public bind.

## Envelope

Every message — pub/sub, retained, or stream — is one JSON object:

```json
{ "v": 1, "ts": 1782971016.4, "src": "supervisor", "topic": "fleet.supervisor.tick",
  "type": "update", "data": { … } }
```

`src` is the producing loop; `topic` doubles as the pub/sub channel and the
retained-key suffix; `data` is the payload the contract below pins per topic.

## Delivery classes

| Class | Producer call | Consumer call | Semantics |
| --- | --- | --- | --- |
| **telemetry** | `publish(topic, data)` | `retained(topic)` + SUBSCRIBE | at-most-once, drop-safe; a retained last-value (TTL) lets a late subscriber render immediately |
| **events** | `emit(stream, data)` | `read_group(stream, group, consumer)` + `ack()` | durable (capped stream), at-least-once, replayable, per-consumer offsets |

Keys: retained values live at `retain:<topic>`; streams at `stream:<name>`.

## Topics (the catalog)

### `fleet.supervisor.tick` — telemetry — **live (this PR's edge)**
Producer: the **supervisor**, once per beat (~60 s). Consumer: **discobot-live**'s
ferris wheel (`loop_dashboard`), which reads `retained("fleet.supervisor.tick")`
and falls back to its InfluxDB query when the bus has nothing. `data` is the
wheel snapshot, exactly the shape `loop_dashboard.fetch_live` returns:

```json
{ "ok": true, "shadow": false, "lag_s": 0.2, "budget_free_pct": 92,
  "by_cron": 6, "by_event": 38, "by_backstop": 2,
  "doit_executed": 9, "doit_uptodate": 41, "fires": 46,
  "last_tick_epoch": 1782970950.0, "last_event_epoch": 1782970800.0 }
```

Publish with `ttl≈180` (≫ the 60 s beat, so one missed tick doesn't blank the
wheel, but a stopped supervisor expires the value → the wheel shows "stopped").

### `fleet.telemetry` — event (durable stream) — **sink live (this PR)**
Producer: any loop that wants a metric *persisted* — `emit("fleet.telemetry",
{...})`. Consumer: the **DuckDB sink** (`ops/telemetry_sink.py`, group
`duckdb-sink`) drains it into a local `.db` with a retention window, the storage
half of obsidian-automations#179. `data` is free-form per producer; the
envelope's `src`/`type`/`ts` are stored alongside.

A producer that also wants a **live** readout publishes the retained telemetry
topic *and* emits here — retained pub/sub feeds the real-time dashboard (no store
round-trip), the stream feeds history/analytics. e.g. the supervisor may
`publish("fleet.supervisor.tick", snap)` (the wheel) **and**
`emit("fleet.telemetry", {"metric": "supervisor.tick", **snap})` (the sink).
Real-time and retention are decoupled: neither is in the other's hot path.

### Planned (follow-on PRs — listed so producers can aim)
- `fleet.tommybot.telemetry` — telemetry — bridge tommybot's `TOMMYBOT_LIVE_FILE`
  (live.md) onto the bus so the chat panel can subscribe instead of poll.
- `fleet.ops.posted` — event — discobot-live emits when it edits a panel (audit).
- `fleet.discord.reaction` — event — the gateway emits Discord reactions; the
  taste-training loop consumes them as feedback (durable stream, replayable).

## Producer/consumer obligations

- **Producers** publish/emit fire-and-forget and never await a consumer. Wrap
  every call so a bus error is logged, not raised (the reference client does).
- **Consumers** treat a `None`/`[]` return as "bus quiet" and fall back to their
  direct source. Never gate a tick on the bus being up.
- **New topic?** Add it here first (name, class, `data` shape, producer,
  consumers), then implement. The catalog is the source of truth.
