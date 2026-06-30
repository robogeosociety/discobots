#!/usr/bin/env bash
# run.sh — start (or restart) discobot containers on the mini.
#
# Resolves each bot's secrets from the host's existing config (no secret enters
# the repo or an image), translates host-local service URLs to
# host.docker.internal, and `docker run`s each bot with --restart unless-stopped.
# Re-running a bot recreates its container (picks up rotated secrets/new image).
#
#   ops/run.sh                  # start all bots: digest, github, watcher, transit
#   ops/run.sh digest           # just one
#
# Secret sources (host-side, unchanged from the old Nomad wrappers):
#   grafana/.env   DISCORD_WEBHOOK_URL                       (all bots)
#   ask-dash/.env  INFLUX_URL / INFLUX_READ_TOKEN / INFLUX_ORG  (digest)
#   `gh auth token`                                          (github)
#   transit_tracker/.local/service.yaml  oba_api_key         (transit)
set -euo pipefail
export PATH="$HOME/.orbstack/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

GRAFANA_ENV="$HOME/dev/observability/grafana/.env"
ASKDASH_ENV="$HOME/dev/observability/ask-dash/.env"
TRANSIT_SVC="$HOME/dev/transit_tracker/.local/service.yaml"
TZ_VAL="America/Los_Angeles"
HOSTGW="host.docker.internal"   # how a container reaches the mini's localhost

docker info >/dev/null 2>&1 || {
  echo "run.sh: docker engine unreachable — is OrbStack running? (\`orb start\`)" >&2
  exit 1
}

# dotget <file> <KEY> — value of KEY in a KEY=VALUE dotenv (quotes stripped).
dotget() {
  grep -E "^$2=" "$1" 2>/dev/null | head -1 | cut -d= -f2- \
    | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}
# hostify — rewrite //localhost or //127.0.0.1 to //host.docker.internal.
hostify() { sed -e "s#//localhost#//$HOSTGW#g" -e "s#//127\.0\.0\.1#//$HOSTGW#g"; }

common_run=(--restart unless-stopped --add-host "$HOSTGW:host-gateway" -e "TZ=$TZ_VAL")

start_digest() {
  local url token org webhook
  url="$(dotget "$ASKDASH_ENV" INFLUX_URL)"; url="${url:-http://localhost:8086}"
  url="$(printf '%s' "$url" | hostify)"
  token="$(dotget "$ASKDASH_ENV" INFLUX_READ_TOKEN)"
  org="$(dotget "$ASKDASH_ENV" INFLUX_ORG)"; org="${org:-home}"
  webhook="$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_URL)"
  [ -n "$token" ]   || { echo "digest: INFLUX_READ_TOKEN missing in ask-dash/.env" >&2; return 1; }
  [ -n "$webhook" ] || { echo "digest: DISCORD_WEBHOOK_URL missing in grafana/.env" >&2; return 1; }
  docker rm -f discobot-digest >/dev/null 2>&1 || true
  docker run -d --name discobot-digest "${common_run[@]}" \
    -e "INFLUXDB_URL=$url" -e "INFLUXDB_TOKEN=$token" -e "INFLUXDB_ORG=$org" \
    -e "DEV_STATUS_URL=http://$HOSTGW:8077" \
    -e "DISCORD_WEBHOOK_URL=$webhook" \
    discobot-digest:latest >/dev/null
  echo "started discobot-digest (weekly Mon 08:15 $TZ_VAL)"
}

start_github() {
  local webhook ghtoken
  webhook="$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_URL)"
  ghtoken="$(gh auth token 2>/dev/null || true)"
  [ -n "$webhook" ] || { echo "github: DISCORD_WEBHOOK_URL missing in grafana/.env" >&2; return 1; }
  [ -n "$ghtoken" ] || { echo "github: \`gh auth token\` empty — run \`gh auth login\` on the mini" >&2; return 1; }
  docker rm -f discobot-github >/dev/null 2>&1 || true
  docker run -d --name discobot-github "${common_run[@]}" \
    -e "DISCORD_WEBHOOK_URL=$webhook" -e "GH_TOKEN=$ghtoken" \
    -v discobot-github-state:/root/.local/share/github-discord \
    discobot-github:latest >/dev/null
  echo "started discobot-github (every 30 min; state in volume discobot-github-state)"
}

start_watcher() {
  local webhook
  webhook="$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_URL)"
  [ -n "$webhook" ] || { echo "watcher: DISCORD_WEBHOOK_URL missing in grafana/.env" >&2; return 1; }
  docker rm -f discobot-watcher >/dev/null 2>&1 || true
  docker run -d --name discobot-watcher "${common_run[@]}" \
    -e "DEV_STATUS_URL=http://$HOSTGW:8077" \
    -e "DISCORD_WEBHOOK_URL=$webhook" \
    discobot-watcher:latest >/dev/null
  echo "started discobot-watcher (daemon, polls dev-status)"
}

start_transit() {
  # Posts to the dedicated transit channel (DISCORD_WEBHOOK_TRANSIT), falling
  # back to the general webhook. OBA key comes from transit_tracker's config.
  local webhook oba
  webhook="$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_TRANSIT)"
  webhook="${webhook:-$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_URL)}"
  oba="$(grep -E '^[[:space:]]*oba_api_key:' "$TRANSIT_SVC" 2>/dev/null | head -1 \
        | sed -E 's/^[[:space:]]*oba_api_key:[[:space:]]*//; s/[[:space:]]*(#.*)?$//; s/^["'\'']//; s/["'\'']$//')"
  [ -n "$oba" ] && [ "$oba" != "TEST" ] || { echo "transit: no real oba_api_key in $TRANSIT_SVC" >&2; return 1; }
  [ -n "$webhook" ] || { echo "transit: no DISCORD_WEBHOOK_TRANSIT/URL in grafana/.env" >&2; return 1; }
  docker rm -f discobot-transit >/dev/null 2>&1 || true
  docker run -d --name discobot-transit "${common_run[@]}" \
    -e "OBA_API_KEY=$oba" -e "DISCORD_WEBHOOK_URL=$webhook" \
    discobot-transit:latest >/dev/null
  echo "started discobot-transit (every 5 min)"
}

start_skills() {
  # Announces the fleet's Claude Code skills to #skills (DISCORD_WEBHOOK_SKILLS,
  # falling back to the general webhook). Reads the skill inventory from the
  # host's ~/.claude/{skills,plugins} mounted read-only — both are under $HOME,
  # which OrbStack mounts fine (unlike /Volumes/*). State (known skills) lives in
  # the named volume discobot-skills-state.
  local webhook
  webhook="$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_SKILLS)"
  webhook="${webhook:-$(dotget "$GRAFANA_ENV" DISCORD_WEBHOOK_URL)}"
  [ -n "$webhook" ] || { echo "skills: no DISCORD_WEBHOOK_SKILLS/URL in grafana/.env" >&2; return 1; }
  [ -d "$HOME/.claude/skills" ] || { echo "skills: $HOME/.claude/skills not found on host" >&2; return 1; }
  docker rm -f discobot-skills >/dev/null 2>&1 || true
  docker run -d --name discobot-skills "${common_run[@]}" \
    -e "DISCORD_WEBHOOK_URL=$webhook" \
    -e "SKILLS_GLOBAL_DIR=/claude/skills" -e "SKILLS_PLUGINS_DIR=/claude/plugins" \
    -v "$HOME/.claude/skills:/claude/skills:ro" \
    -v "$HOME/.claude/plugins:/claude/plugins:ro" \
    -v discobot-skills-state:/root/.local/share/skills-discord \
    discobot-skills:latest >/dev/null
  echo "started discobot-skills (new-skill check every 3h + daily spotlight; state in volume discobot-skills-state)"
}

bots=("$@")
[ ${#bots[@]} -eq 0 ] && bots=(digest github watcher transit skills)
for b in "${bots[@]}"; do
  case "$b" in
    digest)  start_digest ;;
    github)  start_github ;;
    watcher) start_watcher ;;
    transit) start_transit ;;
    skills)  start_skills ;;
    *) echo "run.sh: unknown bot '$b' (digest|github|watcher|transit|skills)" >&2; exit 2 ;;
  esac
done
