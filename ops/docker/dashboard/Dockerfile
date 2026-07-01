# discobot-dashboard — long-running daemon that renders the #ops *dynamic
# dashboard*: ONE Discord message, edited in place on each poll of dev-status
# (no reposts). Like watcher it runs its own loop (--interval), not cron.
#
# The message id + content signature persist in the named volume mounted at
# /state, so a restart reconciles the existing message instead of double-posting.
# Build context = ops/ :  docker build -f docker/dashboard/Dockerfile -t discobot-dashboard .
FROM discobot-base:latest
CMD ["python", "/app/ops_dashboard.py", "--interval", "30", "--iterations", "0", "--state", "/state/ops.json"]
