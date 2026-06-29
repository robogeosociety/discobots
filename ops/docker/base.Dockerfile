# discobot-base — shared image for the Python Discord automations.
#
# Built on the mini (native linux/arm64 under OrbStack). Carries the bot
# scripts + their shared deps + supercronic (container-idiomatic cron used by
# the periodic bots). Per-bot images (docker/<bot>/Dockerfile) extend this.
#
# Build context is ops/ so the COPY below can reach the scripts:
#   docker build -f docker/base.Dockerfile -t discobot-base:latest .
FROM python:3.12-slim

# Shared runtime deps (httpx for Discord/HTTP, influxdb-client for digest).
RUN pip install --no-cache-dir httpx influxdb-client

# supercronic — runs a crontab as an ordinary (non-root-needed) process, logs to
# stdout, no PID-1/syslog assumptions. The periodic bots use it; watcher doesn't.
ARG SUPERCRONIC_VERSION=v0.2.33
ARG TARGETARCH=arm64
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN chmod +x /usr/local/bin/supercronic

WORKDIR /app
# All bot scripts live in one base image; each per-bot image just selects one.
COPY digest.py transit_discord.py github_discord.py watcher.py /app/
