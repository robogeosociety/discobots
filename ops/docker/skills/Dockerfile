# discobot-skills — announce the fleet's Claude Code skills to #skills.
# New-skill check every few hours + one daily 💡 spotlight (see crontab).
# Reads the skill inventory from ~/.claude/{skills,plugins} mounted read-only by
# run.sh; state (known skills) is a named volume at /root/.local/share/skills-discord.
# Build context = ops/ :  docker build -f docker/skills/Dockerfile -t discobot-skills .
FROM discobot-base:latest
COPY docker/skills/crontab /app/crontab
# -no-reap: OrbStack's VM kernel makes supercronic's PID-1 zombie reaper fatal
# ("Failed to fork exec"); we don't spawn orphans, so disable it.
CMD ["supercronic", "-no-reap", "/app/crontab"]
