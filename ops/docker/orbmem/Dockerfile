# discobot-orbmem — live OrbStack per-container memory treemap → #dashboards.
# Daemon (discokit.daemon.serve). Reads per-container memory from InfluxDB
# (docker_container_mem, `system` bucket) and edits one Discord message as OpsBot.
# Migrated from observability-config/discord-orbstack-mem.
# Build context = ops/ :  docker build -f docker/orbmem/Dockerfile -t discobot-orbmem .
FROM discobot-base:latest
CMD ["python", "/app/orbmem.py"]
