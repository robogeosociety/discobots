# discobot-github — GitHub activity → Discord, every 30 min.
# Needs the `gh` CLI (github_discord.py shells out to `gh api`); gh reads its
# token from the GH_TOKEN env injected by run.sh. State (seen event ids) is
# persisted via a named volume mounted at /root/.local/share/github-discord.
# Build context = ops/ :  docker build -f docker/github/Dockerfile -t discobot-github .
FROM discobot-base:latest

ARG GH_VERSION=2.62.0
ARG TARGETARCH=arm64
ADD https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${TARGETARCH}.tar.gz /tmp/gh.tgz
RUN tar -xzf /tmp/gh.tgz -C /tmp \
 && mv /tmp/gh_${GH_VERSION}_linux_${TARGETARCH}/bin/gh /usr/local/bin/gh \
 && rm -rf /tmp/gh*

COPY docker/github/crontab /app/crontab
# -no-reap: see digest/Dockerfile (OrbStack VM kernel + supercronic reaper).
CMD ["supercronic", "-no-reap", "/app/crontab"]
