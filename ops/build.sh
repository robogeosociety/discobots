#!/usr/bin/env bash
# build.sh — build all discobot images on the mini (native linux/arm64 under
# OrbStack). Build context is ops/ (this dir) so the Dockerfiles' COPY of the
# bot scripts resolves. Run after `git pull` on the mini (see `just deploy`).
set -euo pipefail
export PATH="$HOME/.orbstack/bin:$PATH"
cd "$(cd "$(dirname "$0")" && pwd)"   # ops/

docker info >/dev/null 2>&1 || {
  echo "build.sh: docker engine unreachable — is OrbStack running? (\`orb start\`)" >&2
  exit 1
}

echo "==> discobot-base"
docker build -f docker/base.Dockerfile -t discobot-base:latest .

# live replaces dashboard/loop/embed in the default set; the three stay
# buildable for rollback. minimem/orbmem/heatmap are the #dashboards live panels.
for bot in digest github transit watcher skills live minimem orbmem heatmap dashboard loop embed; do
  echo "==> discobot-$bot"
  docker build -f "docker/$bot/Dockerfile" -t "discobot-$bot:latest" .
done

echo "==> built images:"
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep '^discobot' | sort
