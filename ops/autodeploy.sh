#!/usr/bin/env bash
# discobots CD — the mini-side auto-deploy poller (run by launchd every ~2 min,
# com.discobots.autodeploy). On each tick: if origin/main has moved, ff-merge it,
# rebuild the images, (re)start the default bots, and refresh the live #discobots
# inventory panel from ops/fleet.toml. Best-effort and logged: a hiccup skips this
# tick, it never wedges. Idempotent — a no-move tick is a cheap fetch + exit.
#
# This is the CD half of the pipeline: CI (.github/workflows/ci.yml) gates a PR,
# Auto-merge lands it on main, and THIS ships main to the mini within ~2 min —
# and repaints the #discobots board so the inventory tracks the fleet.
#
# Install/load it on the mini:  just autodeploy-install   (see ops/deploy/*.plist)

set -uo pipefail
export PATH="$HOME/.orbstack/bin:$PATH"

REPO="/Volumes/dev/discobots"
GRAFANA_ENV="$HOME/dev/observability/grafana/.env"
STAMP() { date '+%F %T'; }

cd "$REPO" 2>/dev/null || { echo "[autodeploy $(STAMP)] repo $REPO not mounted — skip"; exit 0; }

git fetch --quiet origin main 2>/dev/null || { echo "[autodeploy $(STAMP)] fetch failed — skip"; exit 0; }
local_sha=$(git rev-parse HEAD 2>/dev/null)
remote_sha=$(git rev-parse origin/main 2>/dev/null)
[ -n "$remote_sha" ] || exit 0
[ "$local_sha" = "$remote_sha" ] && exit 0  # nothing new — the common case, cheap

echo "[autodeploy $(STAMP)] main ${local_sha:0:9} → ${remote_sha:0:9} — deploying"

# Only touch a clean checkout on main (never clobber a hand-edit / detached HEAD).
[ "$(git symbolic-ref --short -q HEAD)" = "main" ] || { echo "[autodeploy] not on main — skip"; exit 1; }
git diff --quiet && git diff --cached --quiet || { echo "[autodeploy] dirty tree — skip"; exit 1; }
git merge --ff-only --quiet origin/main || { echo "[autodeploy] ff-merge failed — skip"; exit 1; }

ops/build.sh || { echo "[autodeploy] build failed — leaving the running bots as-is"; exit 1; }
ops/run.sh || echo "[autodeploy] run.sh returned nonzero (a bot may still need a secret) — continuing"

# Repaint the live #discobots inventory panel from fleet.toml (edits in place).
w=$(sed -n 's/^DISCORD_WEBHOOK_DISCOBOTS=//p' "$GRAFANA_ENV" 2>/dev/null | head -1)
[ -n "$w" ] || w=$(sed -n 's/^DISCORD_WEBHOOK_OPS=//p' "$GRAFANA_ENV" 2>/dev/null | head -1)
[ -n "$w" ] || w=$(sed -n 's/^DISCORD_WEBHOOK_URL=//p' "$GRAFANA_ENV" 2>/dev/null | head -1)
if [ -n "$w" ]; then
  docker run --rm -v discobot-fleet-status-state:/state -e DISCORD_WEBHOOK_DISCOBOTS="$w" \
    discobot-live:latest python3 /app/fleet_status.py --discord --state /state/fleet.json \
    || echo "[autodeploy] inventory panel refresh failed — non-fatal"
else
  echo "[autodeploy] no #discobots/#ops webhook in grafana/.env — inventory panel skipped"
fi

echo "[autodeploy $(STAMP)] deployed ${remote_sha:0:9}"
