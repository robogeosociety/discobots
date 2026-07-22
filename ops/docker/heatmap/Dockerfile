# discobot-heatmap — live Claude token-usage heatmap → #dashboards (edit-in-place).
# Daemon (discokit.daemon.serve). Reads the `tokens` series from InfluxDB
# (`claude_code` bucket) and edits one Discord message as OpsBot. Migrated from
# observability-config/discord-claude-heatmap.
# Build context = ops/ :  docker build -f docker/heatmap/Dockerfile -t discobot-heatmap .
FROM discobot-base:latest
CMD ["python", "/app/claude_heatmap.py"]
