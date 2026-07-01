# discobot-loop — long-running daemon that renders the *supervisor-loop ferris wheel* into #ops:
# ONE Discord message, edited in place every ~60s from the InfluxDB `supervisor_tick` telemetry — a
# spinning ASCII wheel (◉ last tick + ✦ last event labeled, trigger counts, lag/shadow health). Like
# the #ops dashboard it runs its own loop (--interval), not cron, and posts to the same #ops webhook.
#
# The message id + content signature persist in the named volume mounted at /state, so a restart
# reconciles the existing message instead of double-posting.
# Build context = ops/ :  docker build -f docker/loop/Dockerfile -t discobot-loop .
FROM discobot-base:latest
CMD ["python", "/app/loop_dashboard.py", "--interval", "60", "--iterations", "0", "--state", "/state/loop.json"]
