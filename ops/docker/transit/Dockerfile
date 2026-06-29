# discobot-transit — transit alerts → Discord, every 5 min.
# DISABLED: transit_discord.py has a documented KNOWN BUG (OBA
# situations-for-agency). Image is built so it's ready, but run.sh does NOT
# start it by default. See ops/README.md.
# Build context = ops/ :  docker build -f docker/transit/Dockerfile -t discobot-transit .
FROM discobot-base:latest
COPY docker/transit/crontab /app/crontab
CMD ["supercronic", "/app/crontab"]
