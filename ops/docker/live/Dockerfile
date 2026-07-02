# discobot-live — the discobots inner loop: all three #ops dashboards
# (dashboard / loop / embed) as recurring Jobs on ONE asyncio event loop
# (discokit.live), in one container instead of three. Each keeps its own
# cadence via env (OPS/LOOP/EMBED_DASH_INTERVAL); state paths point into the
# three ADOPTED state volumes (see run.sh) so it keeps editing the same three
# Discord messages the standalone daemons owned — no reposts on cutover.
#
# Replaces discobot-dashboard + discobot-loop + discobot-embed; those images
# stay buildable for rollback (`just up dashboard loop embed`).
# Build context = ops/ :  docker build -f docker/live/Dockerfile -t discobot-live .
FROM discobot-base:latest
CMD ["python", "/app/live_service.py"]
