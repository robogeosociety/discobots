# Discord channel audit — 2026-07-02

A full index of the private `tommyroar` guild (`1480240435585618064`, 15 text channels),
sampled up to ~300 recent messages/channel via the bot token on the mini. Goal: keep Grafana,
and **enhance** how Grafana/InfluxDB data feeds discobots' Discord surfaces — so this catalogs the
**noisy bots**, **miscategorized feeds**, and **missing displays/images** to fix.

## Channel map

| Channel | msgs/day | Dominant poster | Verdict |
| --- | --: | --- | --- |
| #ops | ~500 | `Ops Alerts` webhook (97%) | 🔴 Firehose — PR + health + Grafana mixed |
| #transit | ~62 | `Transit Bot` + leaks | 🔴 Loudest; ~37% off-topic |
| #obsidian | ~56 | `Obsidian Notes` (41%) + 2 QA agents | 🟡 Busy; no deeplinks; QA overlap |
| #ops-watcher | ~278 | `Ops Watcher` up/down | 🟡 Repetitive; 0 edit-in-place; dup of #ops |
| #general | ~11 | `Grafana` 56% + `Spidey Bot` 26% | 🔴 Alert firehose mislabeled "general" |
| #campsites | ~10 | `Campsite Bot` | 🟢 On-topic; no trend context |
| #maps | ~10 | tommyroar ↔ MapBot | 🟢 Gold standard (real map renders) |
| #trips | ~7 | tommyroar ↔ cc-trips | 🟢 Healthy; zero visuals |
| #ops-digest | ~6 | `Ops Digest` webhook | 🟢 Best-formatted (template) |
| #weather | ~6 | `Grafana` (lightning only) | 🟡 Flapping; 5/6 rules never shipped |
| #dev | ~2 | (4 msgs) | ⚪ Orphaned; `cc-home` misrouted here |
| #mountain | ~0 (5.4d) | `Captain Hook` (2 test msgs) | ⚪ Dead since setup |
| #github | ~0 (5d) | — | ⚪ Empty — PR events land in #ops/#transit |
| #alerts | 0 | — | ⚪ Empty — Grafana's "Ops Alerts" belongs here |
| #ask-grafana | 0 | — | ⚪ Empty — query surface never built |

## Root cause: webhook routing

Verified webhook → channel targets (from `observability/grafana/.env`):

| Env key | Target |
| --- | --- |
| `DISCORD_WEBHOOK_URL` (general) | **#ops** |
| `DISCORD_WEBHOOK_DIGEST`, `_URL_DIGEST`, `_URL_CLAUDE` | #ops |
| `DISCORD_WEBHOOK_TRANSIT` | #transit |
| `DISCORD_WEBHOOK_WEATHER`, `_URL_WEATHER` | #weather |
| `DISCORD_WEBHOOK_OBSIDIAN` | #obsidian |

`ops/run.sh` points `github`, `watcher`, and `digest` all at the general `DISCORD_WEBHOOK_URL`
(→ #ops), so three unrelated feeds pile into #ops. The purpose-built channels (#github, #alerts,
#ops-watcher, #ops-digest) are fed — when at all — by the **legacy `observability` Nomad jobs**
(`Ops Watcher`, `Ops Digest`, …), which still run alongside the newer discobots containers. Net
effect: **duplicate senders + everything defaulting to #ops/#general/#transit.**

## Findings

### Noisy bots
1. **#ops ~500/day** — one shared webhook, per-event posts, almost no edit-in-place (11/500). #transit
   (~62) and #general repeat the pattern.
2. **#ops-watcher ~278/day** discrete up/down posts — collapse to one continuously-edited status panel.
3. **Flapping, no debounce** — #weather lightning fired **14 FIRING/RESOLVED cycles in 5 days**; #general
   and #transit show the same churn. Grafana rules are missing `for:` durations.

### Miscategorized
1. **GitHub "PR Opened" events land in #ops _and_ #transit**; **#github is empty**.
2. **Grafana's webhook is named "Ops Alerts" but posts to #ops**, not the empty **#alerts**.
3. **Duplicate senders**: legacy Nomad `Ops Watcher`→#ops-watcher / `Ops Digest`→#ops-digest run
   alongside discobots containers (→#ops).
4. **Bot "I'm online" heartbeats** land in #general (7 bots) and #dev (`cc-home` announced in #dev, not #home).
5. **Two Q&A agents** (tommybot + claudesidian) both answer in #obsidian.

### Missing images / displays
- **Real maps (pipeline exists in #maps):** **#trips has zero visuals** despite a vault airports-map
  asset going unused — clearest "should have an image." #maps only misses a map on transit-alert replies.
- **Text-native discokit visuals unused:** alert embeds (weather, campsites, transit, Grafana FIRING)
  are bare text — no current-value-vs-threshold field, no braille sparkline. #ops-watcher shows raw
  labels (`port-5196`), no uptime%/flap-count.
- **#obsidian answers** carry no `obsidian://` deeplinks / backlink graph (discokit Phase 3 unused).
- **#weather:** 5 of 6 Tempest rules (wind/rain/freeze/heat/pressure, PR #121) never shipped.
- **#mountain:** no continuous signal — silence == broken.

## Fix backlog (prioritized)

**Track A — Fix routing & kill duplicates** *(cleanup; cross-repo)*
- Dedicated channel webhooks: PR → #github, Grafana alerts → #alerts, health → #ops-watcher.
- Point `ops/run.sh` feeds at the dedicated keys (fallback to general).
- Retire the duplicate legacy `observability` Nomad discord jobs.

**Track B — Collapse noise into live panels** *(discobots; discokit)*
- One `discokit` edit-in-place status panel for #ops-watcher services (chip rows) and #transit per-line.
- Add `for:` debounce to the flapping weather/infra Grafana rules (observability-config).

**Track C — Enrich alerts with data visuals** *(later)*
- Current-value-vs-threshold fields + InfluxDB-backed braille sparklines on Grafana/weather/campsites/
  transit embeds (`discokit.graph`).

**Track D — Real maps for #trips** *(later)*
- Wire cc-trips to the #maps render pipeline; surface the vault airports-map asset.

**Housekeeping:** repurpose or retire #dev / #ask-grafana / #mountain; single-owner #obsidian Q&A;
finish PR #121; relocate bot boot heartbeats.
