# discobot-digest — weekly ops digest (Mon 08:15 America/Los_Angeles).
# Build context = ops/ :  docker build -f docker/digest/Dockerfile -t discobot-digest .
FROM discobot-base:latest
COPY docker/digest/crontab /app/crontab
# -no-reap: OrbStack's VM kernel makes supercronic's PID-1 zombie reaper fatal
# ("Failed to fork exec"); we don't spawn orphans, so disable it.
CMD ["supercronic", "-no-reap", "/app/crontab"]
