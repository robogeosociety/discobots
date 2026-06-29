# discobot-digest — weekly ops digest (Mon 08:15 America/Los_Angeles).
# Build context = ops/ :  docker build -f docker/digest/Dockerfile -t discobot-digest .
FROM discobot-base:latest
COPY docker/digest/crontab /app/crontab
CMD ["supercronic", "/app/crontab"]
