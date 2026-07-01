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
| **dashboard** | `discobot-dashboard` | daemon (poll loop, 30 s) | dev-status `:8077`, Discord | `grafana/.env` `DISCORD_WEBHOOK_OPS` (→ general webhook fallback) |
| **loop** | `discobot-loop` | daemon (poll loop, 60 s) | InfluxDB `:8086`, Discord | `ask-dash/.env` InfluxDB read creds + `grafana/.env` `DISCORD_WEBHOOK_OPS` (→ general webhook fallback, same as dashboard) |

A container reaches the mini's localhost services (InfluxDB, dev-status) via
`host.docker.internal`; `run.sh` rewrites `localhost`/`127.0.0.1` URLs accordingly.

**skills** is the odd one out: it reads files, not a network service. It enumerates the
fleet's shared Claude Code skills — the global `~/.claude/skills/*` plus installed-plugin
skills (`~/.claude/plugins/cache/.../skills/*`) — mounted **read-only** from the host (both
live under `$HOME`, which OrbStack mounts fine; `/Volumes/*` it can't). It posts each
**🆕 new** skill to `#skills` once, and a daily **💡 spotlight** rotates through existing
ones. New-ness is keyed on a version-independent skill id (state in volume
`discobot-skills-state`), so a plugin version bump never re-announces a skill.

**dashboard** is the *dynamic dashboard*: instead of posting a new message per poll, it posts
**one** message and PATCH-edits it in place on each dev-status poll — down-first, colour +
glyph by status, with an `updated <t:…:R>` stamp that self-refreshes client-side (so unchanged
polls make no edit at all). If dev-status goes unreachable it edits the message to a degraded
"showing last known state" rather than going silent. The message id + content signature persist
in the volume `discobot-dashboard-state`, so a restart reconciles the existing message instead
of double-posting. It's the first consumer of **`discokit`**, the shared design-language kit.

**loop** is a second `discokit` dashboard that draws `obsidiand` (the asyncio + pydoit supervisor
loop) as a **spinning ASCII ferris wheel** from its `supervisor_tick` telemetry in the InfluxDB `ops`
bucket. The wheel turns one cabin per minute (so the live message visibly spins as the loop ticks);
the lead cabin **◉ is the last tick** and a **✦ cabin is the last event**, both labeled with a relative
timestamp, over a footer with the trigger split (cron / event / backstop) + fire count and a header
with lag / shadow-mode health (ℹ️ healthy · ⚠️ lag spike · 🔴 stopped). It posts to **#ops** — the same
webhook as the status dashboard, so the loop's health sits beside the service readout. Like the
dashboard it edits **one** message in place (state in volume `discobot-loop-state`) and shows the
last-known wheel if Influx is unreachable. `python3 ops/loop_dashboard.py --dry --demo` spins the
whole sequence locally with no Influx/Discord/deps.

## Layout

```
ops/
  digest.py  github_discord.py  transit_discord.py  watcher.py  skills_discord.py   # notifier bots
  ops_dashboard.py              # the dynamic #ops dashboard (daemon), drives discokit
  loop_dashboard.py             # the #ops supervisor-loop ferris wheel (daemon), drives discokit
  discokit/                     # shared kit: tokens · config · poster · dashboard
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
just up               # start all bots: digest, github, watcher, transit, skills, dashboard
just ps               # list discobot containers + status
just doctor           # confirm the mini's engine is reachable from the Air
just logs github -f   # follow a bot's logs
just run-now digest   # fire a periodic bot once (posts to Discord)
just dry digest       # fire once in dry-run (no post)
just dry dashboard    # preview the dashboard's edit-in-place sequence (no post)
just down [bot...]    # stop/remove containers
just spotlight        # fire the skills bot's 💡 spotlight once now
```

All six bots start by default. `transit` reads OneBusAway's GTFS-Realtime alerts feed
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
