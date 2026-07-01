# discobot-embed — long-running daemon that renders tommybot's *slow embeddings sync* into #ops:
# ONE Discord message, edited in place every ~5min from tommybot's embeddings DB — a growth
# sparkline, per-vault bars, the last-sync timestamp + counts, model, and WAL staleness. No
# InfluxDB: the trickle session itself emits no telemetry, so this reads the DB directly (a
# read-only bind mount at /mnt/tommybot-cache, see run.sh). Like the #ops dashboard/loop it runs
# its own loop (--interval), not cron, and posts to the same #ops webhook.
# Build context = ops/ :  docker build -f docker/embed/Dockerfile -t discobot-embed .
FROM discobot-base:latest
CMD ["python", "/app/embed_dashboard.py", "--interval", "300", "--iterations", "0", "--state", "/state/embed.json"]
