# discobots control plane — drive the bots running on the Mac mini from the Air.
#
# The bots run as OrbStack containers on the always-on mini; this justfile is the
# remote control surface. Every recipe runs over SSH (Tailscale) — docker ops
# execute on the mini with OrbStack's bin on PATH, so the Air needs no docker
# client and the mini's shell profile is left untouched. Deploy = push git +
# pull + build on the mini.
#
# Deploy code + (re)build images:      just deploy
# Start / stop bots:                   just up           |  just down
# Logs / status / manual fire:         just logs github  |  just ps  |  just run-now digest
#
# Bots: digest (weekly), github (30m), watcher (daemon), transit (5m),
#       skills (3h + daily spotlight), live (daemon — the discobots inner loop:
#       all three #ops dashboards, one asyncio process).
#       dashboard/loop/embed are the retired standalone daemons — kept
#       start-able for rollback, out of the default set.

mini_host := "tommydoerr@tommys-mac-mini.tail59a169.ts.net"
mini_repo := "/Volumes/dev/discobots"
# Prefix that makes `docker` resolvable on the mini's non-interactive shell.
dk := "export PATH=$HOME/.orbstack/bin:$PATH; docker"

default:
    @just --list

# --- deploy ---------------------------------------------------------------

# Push local commits, then pull + build images on the mini.
deploy:
    git push
    ssh {{mini_host}} 'cd {{mini_repo}} && git pull --ff-only && ops/build.sh'

# Build images on the mini without pulling (use after a manual mini-side edit).
build:
    ssh {{mini_host}} 'cd {{mini_repo}} && ops/build.sh'

# --- lifecycle ------------------------------------------------------------

# Start/(re)create the enabled bots (or a specific one): just up [BOT...]
up *bots:
    ssh {{mini_host}} 'cd {{mini_repo}} && ops/run.sh {{bots}}'

# Stop + remove a bot's container (default: all of them).
down *bots:
    #!/usr/bin/env bash
    set -euo pipefail
    names="{{bots}}"; [ -z "$names" ] && names="valkey digest github watcher opswatcher transit transit-panel skills live dashboard loop embed"
    cmds=""; for b in $names; do cmds="$cmds docker rm -f discobot-$b;"; done
    ssh {{mini_host}} "export PATH=\$HOME/.orbstack/bin:\$PATH; $cmds" || true

# Restart a running bot's container.
restart bot:
    ssh {{mini_host}} '{{dk}} restart discobot-{{bot}}'

# --- observe --------------------------------------------------------------

# List discobot containers + status.
ps:
    ssh {{mini_host}} '{{dk}} ps -a --filter name=discobot- --format "table {{{{.Names}}\t{{{{.Status}}\t{{{{.Image}}"'

# Tail a bot's logs:  just logs github          (add -f to follow)
logs bot *flags:
    ssh {{mini_host}} '{{dk}} logs {{flags}} --tail 100 discobot-{{bot}}'

# --- fleet (channel-session control plane) --------------------------------

# Manage the Claude Code channel-session fleet (local config on the mini):
#   just fleet ls
#   just fleet session create <name> [--cwd D --model M --effort low|medium|high --emoji E]
#   just fleet session set-cwd|set-model|restart <name> [arg]
#   just fleet skill ls|link|unlink <name> [skill]
#   just fleet emoji set <name> <emoji>   |   just fleet alias set <name> <pattern...>
fleet *args:
    ssh {{mini_host}} 'cd {{mini_repo}} && python3 ops/fleet.py {{args}}'

# Fire a periodic bot once now (runs its script in the live container).
run-now bot:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{bot}}" in
      digest) s=digest.py;; github) s=github_discord.py;; transit) s=transit_discord.py;;
      skills) s=skills_discord.py;;
      watcher|live|dashboard|loop|embed) echo "{{bot}} is a daemon — use \`just logs {{bot}}\` (or \`just dry {{bot}}\` to preview)" >&2; exit 2;;
      *) echo "unknown bot {{bot}}" >&2; exit 2;; esac
    ssh {{mini_host}} "export PATH=\$HOME/.orbstack/bin:\$PATH; docker exec discobot-{{bot}} python /app/$s"

# Fire the skills bot's daily 💡 spotlight once now (posts an existing skill).
spotlight:
    ssh {{mini_host}} "export PATH=\$HOME/.orbstack/bin:\$PATH; docker exec discobot-skills python /app/skills_discord.py --spotlight"

# Refresh the pinned #discobots fleet-status board from ops/fleet.toml (one-shot; edits in place).
# Normally CI/CD does this on every deploy (ops/autodeploy.sh); this is the manual repaint.
# The wiki page is regenerated + published separately:
#   python3 ops/fleet_status.py --markdown docs/fleet-status.md   (then commit + /wikime)
fleet-status:
    ssh {{mini_host}} 'export PATH=$HOME/.orbstack/bin:$PATH; env="$HOME/dev/observability/grafana/.env"; w=$(sed -n "s/^DISCORD_WEBHOOK_DISCOBOTS=//p" "$env" | head -1); [ -n "$w" ] || w=$(sed -n "s/^DISCORD_WEBHOOK_OPS=//p" "$env" | head -1); [ -n "$w" ] || w=$(sed -n "s/^DISCORD_WEBHOOK_URL=//p" "$env" | head -1); [ -n "$w" ] || { echo "fleet-status: no #discobots/#ops webhook in grafana/.env" >&2; exit 1; }; docker run --rm -v discobot-fleet-status-state:/state -e DISCORD_WEBHOOK_DISCOBOTS="$w" discobot-live:latest python3 /app/fleet_status.py --discord --state /state/fleet.json'

# --- CD: the mini-side auto-deploy poller (ops/autodeploy.sh) ---------------

# Install + load the CD poller on the mini (launchd, every 2 min): ff-merge main,
# rebuild, restart bots, repaint the #discobots board. Templates $HOME into the plist.
autodeploy-install:
    ssh {{mini_host}} 'set -e; export PATH=$HOME/.orbstack/bin:$PATH; p="$HOME/Library/LaunchAgents/com.discobots.autodeploy.plist"; sed "s#/Volumes/dev/discobots#{{mini_repo}}#g" {{mini_repo}}/ops/deploy/com.discobots.autodeploy.plist > "$p"; chmod +x {{mini_repo}}/ops/autodeploy.sh; launchctl bootout gui/$(id -u)/com.discobots.autodeploy 2>/dev/null || true; launchctl bootstrap gui/$(id -u) "$p"; echo "installed com.discobots.autodeploy (StartInterval 120s); log: /tmp/discobots-autodeploy.log"'

# Tail the CD poller log; `just autodeploy-status` shows the launchd job state.
autodeploy-log:
    ssh {{mini_host}} 'tail -n 40 /tmp/discobots-autodeploy.log 2>/dev/null || echo "(no log yet — the poller has not fired)"'

autodeploy-status:
    ssh {{mini_host}} 'launchctl print gui/$(id -u)/com.discobots.autodeploy 2>/dev/null | grep -E "state|last exit|program" || echo "com.discobots.autodeploy not loaded — run \`just autodeploy-install\`"'

# Remove the CD poller from the mini.
autodeploy-uninstall:
    ssh {{mini_host}} 'launchctl bootout gui/$(id -u)/com.discobots.autodeploy 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/com.discobots.autodeploy.plist"; echo "removed com.discobots.autodeploy"'

# Dry-run a periodic bot once (no Discord post) — handy after a deploy.
# (Bots differ: digest uses --dry-run, github/transit use --dry.)
dry bot:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{bot}}" in
      digest) s=digest.py; f=--dry-run;; github) s=github_discord.py; f=--dry;;
      transit) s=transit_discord.py; f=--dry;; skills) s=skills_discord.py; f=--dry;;
      live) s=live_service.py; f="--dry --once";;
      dashboard) s=ops_dashboard.py; f="--dry --demo";;
      loop) s=loop_dashboard.py; f="--dry --demo";;
      embed) s=embed_dashboard.py; f="--dry --demo";;
      chat) s=chat_dashboard.py; f="--dry --demo";;
      watcher) echo "watcher is a daemon — use \`just logs watcher\`" >&2; exit 2;;
      *) echo "unknown bot {{bot}}" >&2; exit 2;; esac
    ssh {{mini_host}} "export PATH=\$HOME/.orbstack/bin:\$PATH; docker exec discobot-{{bot}} python /app/$s $f"

# Prove the remote engine is reachable from the Air.
doctor:
    ssh {{mini_host}} '{{dk}} info --format "engine {{{{.ServerVersion}} on {{{{.Name}}, {{{{.Containers}} containers"'
