# ops — the discobots (OrbStack containers)

Each Discord automation runs as its **own OrbStack container on the Mac mini**, built on the
mini and managed remotely from the MacBook Air via the repo-root [`justfile`](../justfile).
Migrated here from the old unversioned `/Volumes/dev/discord-ops` (raw_exec Nomad jobs).

## Bots

| Bot | Container | Schedule | Reaches | Secrets (host-side, injected at run) |
| --- | --- | --- | --- | --- |
| **digest** | `discobot-digest` | weekly, Mon 08:15 PT | InfluxDB `:8086`, dev-status `:8077`, Discord | `ask-dash/.env` InfluxDB creds + `grafana/.env` webhook |
| **github** | `discobot-github` | every 30 min | GitHub (`gh api`), Discord | `gh auth token` + `grafana/.env` webhook |
| **watcher** | `discobot-watcher` | daemon (poll loop) | dev-status `:8077`, Discord | `grafana/.env` webhook |
| **transit** | `discobot-transit` | every 5 min | OneBusAway GTFS-RT alerts, Discord | transit `service.yaml` OBA key + `DISCORD_WEBHOOK_TRANSIT` |
| **skills** | `discobot-skills` | new-skill check every 3 h + spotlight daily 09:30 PT | host `~/.claude/{skills,plugins}` (ro mounts), Discord | `grafana/.env` `DISCORD_WEBHOOK_SKILLS` (→ general webhook fallback) |
| **live** | `discobot-live` | daemon (one asyncio loop; 5 s / 30 s / 60 s / 5 min jobs) | dev-status `:8077`, InfluxDB `:8086`, host `~/Library/Caches/tommybot` (ro mount, DB + live.json), **the bus `:6379`**, Discord | `ask-dash/.env` InfluxDB read creds + `grafana/.env` `DISCORD_WEBHOOK_OPS` (→ general webhook fallback) |

*(dashboard / loop / embed — the three standalone daemons `live` replaced — stay
buildable + start-able by name for rollback, out of the default set.)*

> **The message bus (`discobot-valkey`) is not a discobot** — it's shared fleet
> infrastructure (the discobots inner loop *and* the obsidian-automations supervisor
> ride it), so it lives as declarative Terraform in **`dev/infra/valkey/`**, not in
> this `run.sh`. `just up` drives the bots; `cd dev/infra/valkey && make apply` (on
> the mini) manages the broker + the `fleet-bus` network + the data volume. `run.sh`
> still attaches `live` to `fleet-bus` by name and keeps an idempotent
> network-ensure so `just up live` is self-sufficient.

A container reaches the mini's localhost services (InfluxDB, dev-status) via
`host.docker.internal`; `run.sh` rewrites `localhost`/`127.0.0.1` URLs accordingly.

**skills** is the odd one out: it reads files, not a network service. It enumerates the
fleet's shared Claude Code skills — the global `~/.claude/skills/*` plus installed-plugin
skills (`~/.claude/plugins/cache/.../skills/*`) — mounted **read-only** from the host (both
live under `$HOME`, which OrbStack mounts fine; `/Volumes/*` it can't). It posts each
**🆕 new** skill to `#skills` once, and a daily **💡 spotlight** rotates through existing
ones. New-ness is keyed on a version-independent skill id (state in volume
`discobot-skills-state`), so a plugin version bump never re-announces a skill.

**live** is the discobots **inner loop** — the level-2 application loop of the fleet-hosting
plan (obsidian-automations#149). One container, one process, one asyncio event loop
(`discokit.live`) hosting the #ops dashboards as recurring `Job`s on their own cadences
(status readout 30 s · supervisor wheel 60 s · embeddings graph 5 min · **tommybot chat
panel 5 s** — polls the tommybot#72 `TOMMYBOT_LIVE_FILE` snapshot and renders the in-flight
answer as it thinks: stage dots, token counter, answer tail; see tommybot docs/live.md for
the contract, and note tommybot's env must set `TOMMYBOT_LIVE_FILE=<cache>/live.json` for
the panel to light up). Ticks run in worker
threads, so a slow Influx query never delays the others, and a throwing tick is logged and
retried next round. It **adopts** the three dashboards' state volumes (mounted at
`/state/{dashboard,loop,embed}`), so cutover keeps editing the same three Discord messages —
no reposts. Cutover: `just down dashboard loop embed && just up live`; rollback is the
reverse. The Phase-4 gateway (discord.py liveliness) attaches to this same loop later.
`python3 ops/live_service.py --dry --once` ticks each dashboard once, printed not posted
(dry mode always uses throwaway `/tmp` state, so it can run inside the live container).

The three dashboard panels `live` hosts, as originally shipped:

**dashboard** is the *dynamic dashboard*: instead of posting a new message per poll, it posts
**one** message and PATCH-edits it in place on each dev-status poll — down-first **emoji-dot
chip rows** (🔴 first, four services per line), with an `updated <t:…:R>` stamp that
self-refreshes client-side (so unchanged polls make no edit at all). If dev-status goes unreachable it edits the message to a degraded
"showing last known state" rather than going silent. The message id + content signature persist
in the volume `discobot-dashboard-state`, so a restart reconciles the existing message instead
of double-posting. It was the first consumer of **`discokit`**, the shared design-language kit —
since the Phase-1 migration *every* bot rides it: `config.webhook()` resolves the webhook,
`Poster` posts (batched, 429 back-off), `notify` keeps the durable seen-id state, and `tokens`
is the one palette (generated from `discokit/tokens.json` by `build_tokens.py` — edit the JSON,
rerun the build, never the outputs). Visualization is **Discord-native text** via
`discokit.graph` — btop-style braille area charts, block sparklines/bars (code-block-safe),
and emoji-dot chip rows (the tokens' `dot` channel) — live-updated by the edit-in-place
dashboards. Cheap string-building, no images. (A Playwright HTML→PNG card spike was built and
shelved by taste review — branch `cc/card-renderer`, PR #19, kept unmerged for history.)

**loop** is a second `discokit` dashboard that draws `obsidiand` (the asyncio + pydoit supervisor
loop) as a **spinning ASCII ferris wheel** from its `supervisor_tick` telemetry. It reads the tick
off the **fleet message bus** first (`fleet.supervisor.tick`, a push the supervisor already
computes — see [`docs/BUS.md`](../docs/BUS.md)) and falls back to querying the InfluxDB `ops` bucket
when the bus is quiet/disabled, so the wheel works with or without the bus. The wheel turns one
cabin per minute (so the live message visibly spins as the loop ticks);
the lead cabin **◉ is the last tick** and a **✦ cabin is the last event**, both labeled with a relative
timestamp, over a footer with the trigger split (cron / event / backstop) + fire count and a header
with lag / shadow-mode health (ℹ️ healthy · ⚠️ lag spike · 🔴 stopped). It posts to **#ops** — the same
webhook as the status dashboard, so the loop's health sits beside the service readout. Like the
dashboard it edits **one** message in place (state in volume `discobot-loop-state`) and shows the
last-known wheel if Influx is unreachable. `python3 ops/loop_dashboard.py --dry --demo` spins the
whole sequence locally with no Influx/Discord/deps.

**embed** graphs tommybot's **embeddings sync** — the deliberately slow, min-RAM `*/15` trickle
(tommybot#57) that replaced the OOM-prone once-daily whole-corpus embed. It reads the embeddings DB
**directly** (a read-only mount of `~/Library/Caches/tommybot`, opened `immutable=1` so it never
contends with the live WAL-mode writer) — no InfluxDB, since the trickle session itself emits no
telemetry. Shows a **braille area chart** of total embedded chunks (btop-style, self-tracked across
polls in its own history file, so the slow climb reads as a rising shoreline), a
**per-vault** bar breakdown (an untouched vault renders as an empty bar — the visible sign the
trickle hasn't reached it yet), the **last sync** (timestamp + embedded/changed/rolled counts,
labeled like the loop wheel's last-tick), the embed model, and staleness (🔴 DB unreachable · ⚠️ no
sync in 30 min — the trickle likely isn't landing · ℹ️ healthy). Posts to **#ops** (same webhook as
dashboard/loop). `python3 ops/embed_dashboard.py --dry --demo` replays a seeding-to-recovered
sequence locally with no DB/Discord/deps.

## Layout

```
ops/
  digest.py  github_discord.py  transit_discord.py  watcher.py  skills_discord.py   # notifier bots
  live_service.py               # the inner loop: all #ops dashboards on ONE asyncio loop (daemon)
  ops_dashboard.py              # #ops status readout — job hosted by live (standalone = rollback)
  loop_dashboard.py             # #ops supervisor-loop ferris wheel — job hosted by live ("")
  embed_dashboard.py            # #ops embeddings-sync graph — job hosted by live ("")
  discokit/                     # shared kit: tokens (generated from tokens.json) · config · poster · notify · dashboard · live · graph · art · bus · guard
  docker/
    base.Dockerfile             # shared python+supercronic, carries all scripts + discokit
    <bot>/Dockerfile + crontab  # per-bot image; periodic bots run supercronic
  build.sh                      # mini: build all images
  run.sh                        # mini: resolve secrets + docker run each bot
  _legacy/                      # the retired run-*.sh + nomad/*.hcl, for history
```

## Operating (from the Air)

Every recipe runs over SSH (Tailscale) — docker executes on the mini with OrbStack's bin on
PATH. No docker client is needed on the Air and the mini's shell profile is untouched, so
there's no setup step.

```sh
just deploy           # git push, then git pull + build images on the mini
just up               # start all bots: digest, github, watcher, transit, skills, live
just ps               # list discobot containers + status
just doctor           # confirm the mini's engine is reachable from the Air
just logs github -f   # follow a bot's logs
just run-now digest   # fire a periodic bot once (posts to Discord)
just dry digest       # fire once in dry-run (no post)
just dry live         # tick each hosted dashboard once, printed not posted
just down [bot...]    # stop/remove containers
just spotlight        # fire the skills bot's 💡 spotlight once now
```

All six bots start by default. First cutover to the inner loop:
`just down dashboard loop embed && just up live` (rollback is the reverse — the
standalone images stay built). `transit` reads OneBusAway's GTFS-Realtime alerts feed
(`/api/gtfs_realtime/alerts-for-agency/<id>.pb`) and posts watched-route alerts to the transit
channel. `skills` watches the fleet's Claude Code skills and posts new/spotlighted ones to
`#skills` (see the bots table above).

## Notes

- **No secrets in git or images.** `run.sh` reads them from the host at `docker run` time;
  rotate a secret → `just up <bot>` to recreate with the new value.
- **Durability:** containers use `--restart unless-stopped`; OrbStack is set to start at login
  on the always-on mini, so bots return after a reboot.
- `github`'s seen-event state lives in the named volume `discobot-github-state` (so it doesn't
  re-post history when the container is recreated).
- `skills` keeps its known-skills state in `discobot-skills-state`; on a **fresh** volume the
  first run seeds the inventory silently and posts a single intro embed instead of announcing
  every pre-existing skill as new. `just dry skills` / `just spotlight` preview without writing.

## Obsidian link redirector (`/o`)

Discord only makes `http(s)` links clickable, never `obsidian://`. So Obsidian note links posted
to Discord (by claudesidian, and the pipelines if you point them at it) use the **redirector**:
`https://tommys-mac-mini.tail59a169.ts.net/o?vault=<v>&file=<percent-encoded path>` — same query
as `obsidian://open`, just under the tailnet https origin; tapping it bounces the browser to
Obsidian.

It's **not a service** — `obsidian-redirect.html` (a one-line client-side JS redirect) is served
**directly by `tailscale serve`**, no process or container. Install/refresh with
`ops/redirect-install.sh` on the mini (stages the page to the internal disk and prints the
one-time `sudo tailscale serve --set-path /o …` — file-serving needs root; it persists across
reboots).
