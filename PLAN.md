# PLAN.md — Local-net InfluxDB tool calls for the Discord bots

Implementation plan for giving the Discord bots **read-only tool calls against a
local-network InfluxDB**, so a user can ask a bot a question and get an answer
grounded in real time-series metrics (e.g. "what's the office temperature?",
"network throughput in the last hour?", "is the M1 mini running hot?").

This repo (`tommyroar/discobots`) is the **registry of record** for which bot
does what and where its config lives, so the *plan* and the *config contract*
live here; the *code* lands in `tommyroar/tommybot` (the bot that already does
tool calls). See [`DISCORD.md`](./DISCORD.md).

> **Scope guardrail:** This stays on the **local network**. InfluxDB is reached
> at a LAN/Tailscale address from a bot running on the same network (Nomad
> service `tommybot`). No metrics data leaves the network, and InfluxDB is never
> exposed publicly. Consistent with tommybot's local-only ethos.

---

## 1. Goal & non-goals

### Goal
Let a Discord subagent answer metric questions by calling a small set of
**curated, parameterized InfluxDB tools** during generation. The model picks a
metric name + time window from an allowlist; the connector fills a templated
query, hits the local InfluxDB HTTP API, and returns a compact text result the
model folds into its answer — exactly like the existing `weather` / `web_search`
connectors, but sourced from our own time-series DB.

### Non-goals
- **No raw query language exposed to the model.** The 4B model does not write
  reliable Flux/InfluxQL, and free-form queries are an injection/cost risk. Tools
  are curated and parameter-bound (see §4).
- **No writes.** The InfluxDB token is read-only and bucket-scoped. The bots
  read metrics; they never record them. (tommybot's old *write* path is already
  removed — `tommybot/metrics.py` is a no-op stub. This is the inverse: read.)
- **No new bot / no new Discord app.** This rides on the existing `tommybot`
  app and its subagent routing; bot-per-purpose stays as-is.
- **No public exposure of InfluxDB.** LAN/Tailscale only.

---

## 2. Where things land (and why here)

| Artifact | Repo / path | Notes |
| --- | --- | --- |
| This plan | `tommyroar/discobots` → `PLAN.md` | Planning home; registry-adjacent. |
| Connector code | `tommyroar/tommybot` → `tommybot/tools.py` | Extends the existing `@tool` registry. |
| Per-subagent allowlist | `tommyroar/tommybot` → `tommybot/agents.py` | `VaultProfile.tools` gains the influx tools for the chosen subagent(s). |
| Config contract | `DISCORD.md` (this repo) | New `INFLUXDB_*` keys recorded in the registry table; **values stay in `tommybot/.env`, never committed**. |
| Token / URL secrets | `~/dev/tommybot/.env` (gitignored) | Read-only token + LAN URL; mirrors how the Discord token is stored. |

The InfluxDB instance itself is owned by the observability stack
(`tommyroar/observability-config`, the Grafana/Influx box). This plan **consumes**
it read-only; it does not change the Influx deployment.

---

## 3. Current state (what we're extending)

tommybot already has a working tool-call mechanism (added recently):

- `tommybot/tools.py`
  - `@tool(name, description, parameters, binds=...)` registers a connector
    (a Python fn + JSON-Schema). `get_tools(names, **context)` resolves a
    per-vault allowlist and binds runtime context (e.g. the active vault).
  - `_http_get(url, *, params, offline_hint)` — `requests` with a short
    `(connect, read)` timeout that catches `ConnectionError`/`Timeout` and
    returns a clean `[… unavailable …]` string, so network tools **degrade
    gracefully when offline**. Non-connectivity errors surface as real errors.
  - Existing connectors: `weather` (wttr.in), `web_search` (DuckDuckGo IA),
    `today` (local `date`), `write_note` (writes into the vault).
- `tommybot/engine.py`
  - `generate(messages, …, tools=…, max_tool_rounds=N)`: hands tool schemas to
    Qwen3 via `apply_chat_template(tools=…)`, parses Hermes-style
    `<tool_call>{…}</tool_call>`, runs the tool, feeds the result back, loops.
- `tommybot/agents.py`
  - `VaultProfile.tools` is the per-subagent allowlist (e.g. `dev` is sandboxed
    to `today`; `camping` gets `weather`/`web_search`).

**We add one new module of curated InfluxDB tools and register them** — no change
to the tool-call loop, the offline-degradation pattern, or the allowlist model.

---

## 4. Architecture

### 4.1 Data flow

```
Discord msg ──route()──▶ subagent (e.g. "ops"/"home")
                              │
                    engine.generate(tools=[…influx tools…])
                              │  Qwen3 emits <tool_call> metric_latest{...}
                              ▼
                 tommybot/influx.py  ── templated Flux ──▶  http://<lan-host>:8086
                              │                              /api/v2/query (read-only token)
                              │◀── compact text / [unavailable] ──┘
                              ▼
              result fed back; model writes grounded answer
```

### 4.2 Connector design — curated, not free-form

Expose a **fixed catalogue of metrics** (a Python dict), not the query language.
Each catalogue entry maps a friendly name → the measurement/field + a templated
query. The model chooses `metric` (an enum in the JSON-Schema, so it can only
pick a known one) and a `window`; the connector binds those into a server-side
parameterized Flux query.

```python
# tommybot/influx.py  (sketch)
METRICS = {
    "office_temp":   {"measurement": "sensors", "field": "temp_c",  "tags": {"room": "office"}},
    "mini_cpu_temp": {"measurement": "host",    "field": "cpu_temp", "tags": {"host": "m1-mini"}},
    "net_throughput":{"measurement": "net",      "field": "bytes_s", "tags": {}},
    # … allowlist; extend deliberately …
}

@tool(
    "metric_latest",
    "Get the most recent value of a known local metric.",
    {"type": "object",
     "properties": {"metric": {"type": "string", "enum": sorted(METRICS)}},
     "required": ["metric"]},
)
def metric_latest(metric: str) -> str:
    spec = METRICS.get(metric)
    if not spec:
        return f"[unknown metric {metric!r}]"
    return _influx_query(_flux_last(spec))   # offline-graceful via _http_get-style helper

@tool(
    "metric_range",
    "Summarize a known local metric over a time window (min/max/mean).",
    {"type": "object",
     "properties": {
        "metric": {"type": "string", "enum": sorted(METRICS)},
        "window": {"type": "string", "description": "e.g. 1h, 24h, 7d", "default": "1h"}},
     "required": ["metric"]},
)
def metric_range(metric: str, window: str = "1h") -> str:
    ...
```

- **`metric` is an enum** → the model literally cannot reference a measurement
  outside the catalogue.
- **`window` is validated** against `^\d+[mhd]$` before it touches a query;
  reject anything else. Use InfluxDB's parameterized-query support (`params=`)
  rather than string interpolation wherever the API allows.
- The Flux templates are authored by us, one per query shape (`last`, `range`
  aggregate). The model never supplies Flux.

### 4.3 Transport & offline grace

Reuse the established pattern: an `_influx_query()` helper that POSTs to
`/api/v2/query` with `requests`, the short `_HTTP_TIMEOUT`, the read-only token
in the `Authorization: Token …` header, and the **same graceful-offline
contract** as `_http_get` — `ConnectionError`/`Timeout` (the LAN host down, Influx
not running, off-network) returns
`[metrics unavailable — could not reach the metrics server]` instead of hanging
or leaking a traceback. This directly extends the offline-resilience work already
in `tools.py`.

---

## 5. Config & secrets

New keys (values live in `~/dev/tommybot/.env`, **never committed**; recorded in
`DISCORD.md` per repo convention):

| Key | Example | Purpose |
| --- | --- | --- |
| `INFLUXDB_URL` | `http://nas.lan:8086` | LAN/Tailscale address of InfluxDB. |
| `INFLUXDB_TOKEN` | *(read-only token)* | Scoped to the metrics bucket, read-only. |
| `INFLUXDB_ORG` | `home` | InfluxDB 2.x org. |
| `INFLUXDB_BUCKET` | `telegraf` | Default bucket the catalogue reads from. |

If any of these are unset, the influx tools **self-disable** (not registered for
any subagent) so a fresh checkout without metrics config still runs — the same
defensive posture as the rest of the bot.

A placeholder `.env.example` (keys only, no values) is added to `tommybot` so the
contract is discoverable.

---

## 6. Per-subagent allowlist

Metric tools are added to the allowlist of the subagent(s) that should answer
ops/home questions — proposed: an **`ops`/`home`** persona — and **withheld**
from `dev`/`camping`/`gear`, matching the existing sandboxing model. Final
assignment is a one-line change in `agents.py` per the decision in §10.

---

## 7. Phased implementation

1. **Config plumbing** — read `INFLUXDB_*` from env; self-disable if absent.
   Create a **read-only, bucket-scoped** token in InfluxDB. Add `.env.example`.
2. **`_influx_query()` helper** — POST Flux to `/api/v2/query`, token auth,
   short timeout, offline-graceful (mirror `_http_get`). Unit-tested with mocked
   `requests`.
3. **Metric catalogue + tools** — `METRICS` allowlist, `metric_latest` /
   `metric_range` with enum + window validation; templated Flux only.
4. **Register + allowlist** — add tool names to the chosen `VaultProfile.tools`.
5. **Registry + rollout** — record `INFLUXDB_*` in `DISCORD.md`; wire env into
   the Nomad `tommybot` job; verify on-device against the live LAN Influx.

Each phase is independently shippable; the bot is fully functional after each.

---

## 8. Testing

Mirror `tommybot/tests/test_tools.py` (pure logic, no network):

- **Offline grace:** mock `requests.post` to raise `ConnectionError`/`Timeout` →
  asserts the `[metrics unavailable …]` hint, not a raw error.
- **Non-network failure** (HTTP 4xx/5xx) → surfaces a real `[error: …]`, **not**
  masked as offline.
- **Allowlist enforcement:** an unknown `metric` returns `[unknown metric …]`;
  the JSON-Schema `enum` is asserted to equal the catalogue keys.
- **Window validation:** `window="; drop"` (or any non-`\d+[mhd]`) is rejected
  before query construction.
- **Self-disable:** with `INFLUXDB_*` unset, the influx tools are absent from
  every `VaultProfile`.
- **Query shape:** the templated Flux for a catalogue entry references only its
  measurement/field/tags (golden-string assert) — guards against accidental
  cross-measurement reads.

---

## 9. Security model

- **Read-only, bucket-scoped token.** No write/delete capability; least
  privilege. Stored only in the gitignored `.env`.
- **No model-authored queries.** `metric` is an enum; `window` is regex-validated;
  Flux is authored by us and parameter-bound. The model cannot inject query text.
- **Local-net only.** `INFLUXDB_URL` is a LAN/Tailscale host; InfluxDB is not
  internet-exposed. No metrics leave the network.
- **Bounded output.** Aggregate/last queries return a handful of points; results
  are truncated (reuse the 4000-char cap) so a fat series can't blow up a reply.
- **Fails safe.** Missing config → tools self-disable. Unreachable Influx →
  graceful "unavailable". Bad status → real error surfaced, not hidden.
- **Secrets discipline.** Per this repo's rules: no tokens/URLs committed; keys
  recorded in `DISCORD.md`, values in `.env`.

---

## 10. Open questions / decisions to confirm

1. **InfluxDB version.** Plan assumes **2.x** (Flux, `/api/v2/query`, org+bucket,
   token auth). If it's **1.8** (InfluxQL, `/query`, user/pass or v1 token), the
   `_influx_query` helper and templates change shape — same architecture,
   different dialect. *Confirm before Phase 2.*
2. **LAN address & reachability.** The exact `INFLUXDB_URL` (mDNS `.lan`,
   Tailscale MagicDNS, or IP) and whether the Nomad `tommybot` allocation can
   reach it.
3. **Which subagent gets it.** New `ops`/`home` persona vs. attaching to an
   existing one (see §6).
4. **Metric catalogue contents.** The initial `METRICS` allowlist — which
   measurements/fields/tags actually matter (depends on what Telegraf/sensors
   write).
5. **Bucket/retention** the read-only token should be scoped to.

---

## 11. Success criteria

1. A bot answers a metric question (e.g. "what's the office temperature?") with a
   value sourced live from the local InfluxDB, via a `<tool_call>`.
2. With Influx unreachable or off-network, the bot returns a clean "metrics
   unavailable" answer **within the short timeout** — no hang, no traceback.
3. The model cannot read outside the curated catalogue, cannot write, and cannot
   inject query text (covered by tests in §8).
4. A checkout without `INFLUXDB_*` configured runs unchanged (tools self-disable).
5. `DISCORD.md` records the new config contract; no secrets are committed.
