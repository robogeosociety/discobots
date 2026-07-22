# discobot-minimem — live Mac-mini memory treemap → #dashboards (edit-in-place).
# Daemon (its own tick loop via discokit.daemon.serve, --interval default 60s), so
# no supercronic. Reads the dev-status :8077 process snapshot via host.docker.internal
# and edits one Discord message as OpsBot. Migrated from
# observability-config/discord-mini-mem; the discokit watchdog fixes the DNS wedge.
# Build context = ops/ :  docker build -f docker/minimem/Dockerfile -t discobot-minimem .
FROM discobot-base:latest
CMD ["python", "/app/minimem.py"]
