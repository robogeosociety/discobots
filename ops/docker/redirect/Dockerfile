# discobot-redirect — tiny https→obsidian:// deep-link bridge (stdlib only, no
# deps, no secrets). Exposed on the tailnet via `tailscale serve … /o`.
# Build context = ops/ :  docker build -f docker/redirect/Dockerfile -t discobot-redirect .
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY obsidian_redirect.py /app/
EXPOSE 8099
CMD ["python", "/app/obsidian_redirect.py"]
