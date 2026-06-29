# discobots

Canonical home for Tommy's Discord bot configs and integration code.

➡️ **The registry of record is [`DISCORD.md`](./DISCORD.md)** — which Discord app/bot
serves which purpose and where each config lives. No secrets are committed here.

## The discobots

The automations in [`ops/`](./ops/) (digest, github, watcher, transit) run as
**individual OrbStack containers on the always-on Mac mini**, built on the mini and managed
remotely from the MacBook Air. The repo-root [`justfile`](./justfile) is the control plane
(every recipe runs over SSH/Tailscale — nothing to install on the Air, no setup step):

```sh
just deploy    # push + pull + build on the mini
just up        # start the bots   ·   just ps / logs / down to manage
```

See [`ops/README.md`](./ops/README.md) for the per-bot details and operations.
