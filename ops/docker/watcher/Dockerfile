# discobot-watcher — long-running daemon polling the dev-status server and
# posting Discord on state changes. No cron: it runs its own poll loop
# (--interval, default 30s). Had no Nomad job; containerizing makes it durable.
# Build context = ops/ :  docker build -f docker/watcher/Dockerfile -t discobot-watcher .
FROM discobot-base:latest
CMD ["python", "/app/watcher.py"]
