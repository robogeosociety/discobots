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

A container reaches the mini's localhost services (InfluxDB, dev-status) via
`host.docker.internal`; `run.sh` rewrites `localhost`/`127.0.0.1` URLs accordingly.

## Layout

```
ops/
  digest.py  github_discord.py  transit_discord.py  watcher.py   # the bots
  docker/
    base.Dockerfile             # shared python+supercronic, carries all scripts
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
just up               # start all bots: digest, github, watcher, transit
just ps               # list discobot containers + status
just doctor           # confirm the mini's engine is reachable from the Air
just logs github -f   # follow a bot's logs
just run-now digest   # fire a periodic bot once (posts to Discord)
just dry digest       # fire once in dry-run (no post)
just down [bot...]    # stop/remove containers
```

All four bots start by default. `transit` reads OneBusAway's GTFS-Realtime alerts feed
(`/api/gtfs_realtime/alerts-for-agency/<id>.pb`) and posts watched-route alerts to the transit
channel.

## Notes

- **No secrets in git or images.** `run.sh` reads them from the host at `docker run` time;
  rotate a secret → `just up <bot>` to recreate with the new value.
- **Durability:** containers use `--restart unless-stopped`; OrbStack is set to start at login
  on the always-on mini, so bots return after a reboot.
- `github`'s seen-event state lives in the named volume `discobot-github-state` (so it doesn't
  re-post history when the container is recreated).
