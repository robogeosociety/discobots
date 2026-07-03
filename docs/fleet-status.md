# 🛰️ fleet status

_the Discord agent fleet — bots, collectors, data, graphs_

> This page is generated from `ops/fleet.toml` and mirrors the pinned **#ops** Discord panel. The live telemetry is in Discord — **#ops** (loop + supervisor), **#ops-watcher** (dev status), **#transit** (lines). This board is the directory.

## 🤖 Discord ops bots

|  | bot | channel | cadence | what | repo |
| --- | --- | --- | --- | --- | --- |
| ✅ | `obsidian-supervisor` | #ops | live · per-min, edit-in-place | automation loop status: braille activity graph + job health chips | obsidian-automations |
| ✅ | `live` | #ops | daemon · edit-in-place | inner loop — four #ops panels in one asyncio process: ops status, loop ferris-wheel, embeddings sync, tommybot chat | discobots |
| ✅ | `opswatcher` | #ops-watcher | daemon · edit-in-place | dev-status board — one live panel (collapsed the ~278/day up/down spam) | discobots |
| ✅ | `transit-panel` | #transit | daemon · 60s, edit-in-place | per-line Link/transit status from OneBusAway (collapsed the ~62/day alert churn) | discobots |
| ✅ | `github` | #github | every 30 min | new GitHub activity across the tommyroar org (PRs, pushes) | discobots |
| ✅ | `digest` | #ops | weekly · Mon 08:15 PT | InfluxDB health + dev-status weekly digest | discobots |
| ✅ | `skills` | #skills | every 3h + daily 💡 spotlight | new Claude Code skills acquired across the fleet | discobots |
| ✅ | `grafana-alerting` | #alerts | on firing / resolved | platform-health alerts from Grafana's InfluxDB-backed rules | observability-config |

## 🌾 Collectors

|  | collector | source | feeds |
| --- | --- | --- | --- |
| ✅ | `weather` | wttr.in | daily / weekly notes |
| ✅ | `transit` | OneBusAway GTFS-Realtime | #transit panel + daily notes |
| ✅ | `github` | GitHub API | #github + changelog + daily notes |
| ✅ | `mountain` | mountain webcams | daily notes |
| ✅ | `tokens` | CF/token pool status | daily notes |
| ✅ | `campsite-inventory` | campsite mirrors | RGS PRs + camping notes |
| ✅ | `vault_mirror` | git blob-OID sync of the vault | the supervisor event-gate (fans change edges to jobs) |
| ✅ | `obsidian-supervisor jobs` | 22-job asyncio loop | weekly-data, daily-note, asset-graph, the wiki builds, vault snapshot/backup |

## 🗄️ Data sources

|  | source | kind | serves |
| --- | --- | --- | --- |
| ✅ | `InfluxDB · ops bucket` | time-series DB | supervisor telemetry, Grafana dashboards, the #ops loop panel |
| ✅ | `Grafana` | dashboards + alerting | 26 dashboards; firing/resolved rules → #alerts |
| ✅ | `supervisor /ticks + /health` | HTTP JSON (127.0.0.1:8787, tailnet) | the last 480 supervisor_tick frames (lag, fired, doit, budget) — no Grafana round-trip |
| ✅ | `fleet bus · Valkey` | Redis-compatible pub/sub | fleet.supervisor.tick → the #ops ferris wheel (discokit.bus, docs/BUS.md) |
| ✅ | `vault mirror git` | bare --depth-1 mirror | the event source — a SHA move fans change edges to the supervisor's event jobs |

## 📈 Graph kit

| kit | kinds | repo |
| --- | --- | --- |
| `discokit.graph` | braille area · block spark · proportional bar | discobots (ops/discokit/graph.py) |
| `supervisor.textgraph` | zero-anchored braille + spark (count series) | obsidian-automations (supervisor/textgraph.py) |
| `doit asset-graph` | Mermaid DAG (file_dep → target) | obsidian-automations (automations/graph.py) |

Sample of the text-native vocabulary (the real panels edit themselves in place):

```
activity · jobs fired/min
⠀⠀⢀⡀⠀⠀⢀⣾⡄⠀⠀⠀⠀⠀⣀⠀⠀⠀⠀⠀
⣀⣠⣾⣿⣄⣀⣸⣿⣿⣄⣠⣤⣀⣼⣿⣦⣀⣠⣾⣆
memory · mini free %  ▄▅▆▇██▇▅▄▂▁▁▁▂▃▄▅▆▇▇
```

## 🔁 Keeping it in sync

`ops/fleet.toml` is the single source of truth. webhook URLs live in grafana/.env on the mini (the routing SSOT); one feed → one channel.

1. Edit `ops/fleet.toml`.
2. `python3 ops/fleet_status.py --markdown docs/fleet-status.md` — regenerate this page (a test asserts it matches).
3. `just fleet-status` — repost/edit the pinned #ops panel from the same file.
4. Commit; `/wikime` publishes this page to the dev wiki.

_Generated from `ops/fleet.toml` by `ops/fleet_status.py` — do not hand-edit._
