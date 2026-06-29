# discobots control plane — drive the bots running on the Mac mini from the Air.
#
# The bots run as OrbStack containers on the always-on mini; this justfile is the
# remote control surface. It talks to the mini's OrbStack Docker engine over SSH
# (Tailscale) via a docker *context*, and deploys by pushing git + pulling +
# building on the mini.
#
# One-time setup on the Air:           just setup
# Deploy code + (re)build images:      just deploy
# Start / stop bots:                   just up           |  just down
# Logs / status / manual fire:         just logs github  |  just ps  |  just run-now digest
#
# Bots: digest (weekly), github (30m), watcher (daemon), transit (disabled).

mini_host := "tommydoerr@tommys-mac-mini.tail59a169.ts.net"
mini_repo := "/Volumes/dev/discobots"
ctx       := "mini"
# docker on the Air targets the mini's OrbStack engine via the SSH context
# (created by `just setup`). deploy/build/up run scripts *on* the mini over ssh
# (they need host-side secrets); the observe/lifecycle recipes use this context.
docker    := "docker --context " + ctx

default:
    @just --list

# --- one-time setup -------------------------------------------------------

# Install a docker client (pixi) on the Air + create the SSH docker context.
setup:
    command -v docker >/dev/null 2>&1 || pixi global install docker
    docker context inspect {{ctx}} >/dev/null 2>&1 || \
      docker context create {{ctx}} --docker host=ssh://{{mini_host}}
    @echo "context '{{ctx}}' → {{mini_host}}"
    {{docker}} info --format 'engine: {{{{.ServerVersion}}}} ({{{{.Name}}}})'

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

# Stop + remove a bot's container (default: all four).
down *bots:
    #!/usr/bin/env bash
    set -euo pipefail
    names="{{bots}}"
    [ -z "$names" ] && names="digest github watcher transit"
    for b in $names; do {{docker}} rm -f "discobot-$b" 2>/dev/null && echo "removed discobot-$b" || true; done

# Restart a running bot's container.
restart bot:
    {{docker}} restart discobot-{{bot}}

# --- observe --------------------------------------------------------------

# List discobot containers + status.
ps:
    {{docker}} ps -a --filter name=discobot- \
      --format 'table {{{{.Names}}}}\t{{{{.Status}}}}\t{{{{.Image}}}}'

# Tail a bot's logs:  just logs github          (add -f to follow)
logs bot *flags:
    {{docker}} logs {{flags}} --tail 100 discobot-{{bot}}

# Fire a periodic bot once now (runs its script in the live container).
run-now bot:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{bot}}" in
      digest) s=digest.py;; github) s=github_discord.py;; transit) s=transit_discord.py;;
      watcher) echo "watcher is a daemon — use \`just logs watcher\`" >&2; exit 2;;
      *) echo "unknown bot {{bot}}" >&2; exit 2;; esac
    {{docker}} exec discobot-{{bot}} python /app/$s

# Dry-run a periodic bot once (no Discord post) — handy after a deploy.
# (Bots differ: digest uses --dry-run, github/transit use --dry.)
dry bot:
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{bot}}" in
      digest) s=digest.py; f=--dry-run;; github) s=github_discord.py; f=--dry;;
      transit) s=transit_discord.py; f=--dry;;
      watcher) echo "watcher is a daemon — use \`just logs watcher\`" >&2; exit 2;;
      *) echo "unknown bot {{bot}}" >&2; exit 2;; esac
    {{docker}} exec discobot-{{bot}} python /app/$s $f

# Open the OrbStack engine info (proves the remote context works).
doctor:
    {{docker}} info --format 'engine {{{{.ServerVersion}}}} on {{{{.Name}}}}, {{{{.Containers}}}} containers'
