"""discokit.config — resolve a named Discord webhook.

Same resolution the five ops bots each hand-roll today, in one place:
    1. env  DISCORD_WEBHOOK_<NAME>   (e.g. DISCORD_WEBHOOK_OPS)
    2. env  DISCORD_WEBHOOK_URL       (the fleet's general webhook)
    3. ~/dev/observability/grafana/.env  (the single source of truth on the mini)

No secret ever lives in the repo; this only *reads* the host's .env at run time,
exactly like watcher/github/skills/transit/digest do now.
"""

from __future__ import annotations

import os
from pathlib import Path

GRAFANA_ENV = Path.home() / "dev" / "observability" / "grafana" / ".env"


def _read_dotenv(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("\"'")
    return env


def webhook(name: str = "URL", *, env_file: Path = GRAFANA_ENV) -> str | None:
    """Resolve the webhook URL for a logical channel name (e.g. "OPS").

    Checks env vars first (DISCORD_WEBHOOK_<NAME>, then DISCORD_WEBHOOK_URL),
    then the grafana .env for the same two keys. Returns None if unset.
    """
    key = f"DISCORD_WEBHOOK_{name.upper()}"
    for env_key in (key, "DISCORD_WEBHOOK_URL"):
        if os.environ.get(env_key):
            return os.environ[env_key]
    dotenv = _read_dotenv(env_file)
    for env_key in (key, "DISCORD_WEBHOOK_URL"):
        if dotenv.get(env_key):
            return dotenv[env_key]
    return None
