# discobot-transit — transit alerts → Discord, every 5 min.
# Reads OneBusAway's GTFS-Realtime alerts feed, so it needs the protobuf bindings
# on top of the base image's deps.
# Build context = ops/ :  docker build -f docker/transit/Dockerfile -t discobot-transit .
FROM discobot-base:latest
RUN pip install --no-cache-dir gtfs-realtime-bindings
COPY docker/transit/crontab /app/crontab
# -no-reap: see digest/Dockerfile (OrbStack VM kernel + supercronic reaper).
CMD ["supercronic", "-no-reap", "/app/crontab"]
