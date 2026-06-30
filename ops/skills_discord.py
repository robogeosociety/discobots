#!/usr/bin/env python3
"""Announce the fleet's Claude Code skills to the Discord #skills channel.

Two modes:

  (default)      Scan the skill inventory, post any **new** skill the fleet has
                 gained since the last run (🆕). Run often (every few hours).
  --spotlight    Post a 💡 spotlight on one existing, not-recently-featured
                 skill — the "mention old skills occasionally" half. Run ~daily.

The inventory is the set of skills shared across the fleet of Claude Code
agent-bots: the hand-authored global skills in ~/.claude/skills/ plus the skills
provided by installed plugins (~/.claude/plugins/cache/.../skills/). Both trees
live under $HOME so they mount read-only into this container fine (unlike
/Volumes/*, which OrbStack can't reliably mount).

State (which skills are known, when each was first seen, recent spotlights) is a
single JSON file in a named volume, mirroring github_discord.py. New-ness is
keyed on a *version-independent* skill id, so a plugin version bump never
re-announces an existing skill.

No new runtime deps: stdlib + httpx (already in the base image); a tiny
hand-rolled front-matter parser avoids pulling in PyYAML.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

# --- config (env-overridable so the container can point at its mounts) --------
GLOBAL_DIR = Path(
    os.environ.get("SKILLS_GLOBAL_DIR", str(Path.home() / ".claude" / "skills"))
)
PLUGINS_DIR = Path(
    os.environ.get("SKILLS_PLUGINS_DIR", str(Path.home() / ".claude" / "plugins"))
)
STATE_DIR = Path(
    os.environ.get(
        "SKILLS_STATE_DIR", str(Path.home() / ".local" / "share" / "skills-discord")
    )
)
STATE_FILE = STATE_DIR / "state.json"

DISCORD_EMBEDS_PER_REQUEST = 10
DESC_LIMIT = 600  # keep embeds skimmable; full text lives in the SKILL.md
# How long to avoid re-spotlighting a skill (rotate through the catalog).
SPOTLIGHT_COOLDOWN = 8

COLOR_NEW = 0x2ECC71  # green
COLOR_SPOTLIGHT = 0x5865F2  # blurple
COLOR_INIT = 0x95A5A6  # grey


@dataclass(frozen=True)
class Skill:
    key: str  # stable, version-independent id
    name: str  # frontmatter `name` (display)
    description: str  # frontmatter `description`
    source: str  # human label: "global" or "<plugin> plugin"
    since: float  # epoch of SKILL.md mtime (best-effort "available since")


# --- frontmatter ---------------------------------------------------------------
def parse_frontmatter(text: str) -> dict[str, str]:
    """Pull simple `key: value` pairs from a leading `---` YAML front-matter block.

    Skills use single-line scalar values for name/description, so a line parser is
    enough — and keeps us off a YAML dependency. Folded/quoted values are stripped.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line or line[0] in " \t" or ":" not in line:
            continue  # skip nested/continuation lines — we only need top-level scalars
        key, _, val = line.partition(":")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key.strip()] = val
    return out


def read_skill(skill_md: Path, *, key: str, source: str) -> Skill | None:
    try:
        fm = parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    name = fm.get("name") or skill_md.parent.name
    desc = fm.get("description", "").strip()
    if not desc:
        return None
    try:
        since = skill_md.stat().st_mtime
    except OSError:
        since = 0.0
    return Skill(key=key, name=name, description=desc, source=source, since=since)


# --- inventory -----------------------------------------------------------------
def discover_skills() -> dict[str, Skill]:
    """Build the current fleet skill inventory, keyed by stable id."""
    skills: dict[str, Skill] = {}

    # 1. Global, hand-authored skills shared by every bot.
    for skill_md in sorted(GLOBAL_DIR.glob("*/SKILL.md")):
        key = f"global:{skill_md.parent.name}"
        s = read_skill(skill_md, key=key, source="global")
        if s:
            skills[key] = s

    # 2. Installed-plugin skills. cache/ holds exactly the installed plugins, one
    #    version dir each: cache/<marketplace>/<plugin>/<version>/skills/<skill>/SKILL.md
    #    Key excludes <version> so a version bump isn't seen as a new skill.
    for skill_md in sorted(PLUGINS_DIR.glob("cache/*/*/*/skills/*/SKILL.md")):
        parts = skill_md.relative_to(
            PLUGINS_DIR
        ).parts  # cache, mkt, plugin, ver, skills, skill, SKILL.md
        if len(parts) < 7:
            continue
        _, marketplace, plugin, _version, _skills, skill_dir, _ = parts[:7]
        key = f"plugin:{marketplace}/{plugin}/{skill_dir}"
        s = read_skill(skill_md, key=key, source=f"{plugin} plugin")
        if s:
            skills[key] = s

    return skills


# --- state ---------------------------------------------------------------------
def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# --- discord -------------------------------------------------------------------
def get_webhook_url() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_SKILLS") or os.environ.get(
        "DISCORD_WEBHOOK_URL"
    )
    if url:
        return url
    fallback = Path.home() / "dev" / "observability" / "grafana" / ".env"
    if fallback.exists():
        for key in ("DISCORD_WEBHOOK_SKILLS", "DISCORD_WEBHOOK_URL"):
            for line in fallback.read_text().splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    return None


def clip(text: str, limit: int = DESC_LIMIT) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def post_embeds(webhook_url: str, embeds: list[dict], *, dry: bool) -> None:
    for i in range(0, len(embeds), DISCORD_EMBEDS_PER_REQUEST):
        batch = embeds[i : i + DISCORD_EMBEDS_PER_REQUEST]
        if dry:
            print(f"[dry-run] would post {len(batch)} embed(s):")
            for e in batch:
                print(f"  - {e.get('title', '(no title)')}")
            continue
        try:
            resp = httpx.post(webhook_url, json={"embeds": batch}, timeout=10)
            if resp.status_code >= 400:
                print(
                    f"[skills-discord] Discord {resp.status_code}: {resp.text}",
                    file=sys.stderr,
                )
        except httpx.HTTPError as exc:
            print(f"[skills-discord] Discord request failed: {exc}", file=sys.stderr)


def new_skill_embed(s: Skill) -> dict:
    return {
        "title": f"🆕 New skill: {s.name}",
        "description": clip(s.description),
        "color": COLOR_NEW,
        "footer": {"text": f"source: {s.source}  ·  /{s.name}"},
    }


def spotlight_embed(s: Skill) -> dict:
    e: dict = {
        "title": f"💡 Skill spotlight: {s.name}",
        "description": clip(s.description),
        "color": COLOR_SPOTLIGHT,
        "footer": {
            "text": f"source: {s.source}  ·  /{s.name}  ·  an oldie worth remembering"
        },
    }
    if s.since:
        e["timestamp"] = _iso(s.since)
    return e


def _iso(epoch: float) -> str:
    # Avoid datetime.now() noise; just format the given epoch as UTC ISO-8601.
    import datetime

    return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat()


# --- modes ---------------------------------------------------------------------
def run_new(
    inv: dict[str, Skill], state: dict, webhook: str | None, *, dry: bool
) -> None:
    known: dict = state.setdefault("skills", {})

    # First ever run: seed silently and post a single intro, rather than flooding
    # the channel with every pre-existing skill as "new".
    first_run = not state.get("initialized")

    fresh = [s for k, s in inv.items() if k not in known]
    for k, s in inv.items():
        known.setdefault(k, {"first_seen": s.since, "name": s.name})

    state["initialized"] = True
    if not dry:
        save_state(state)

    if first_run:
        names = ", ".join(sorted(s.name for s in inv.values()))
        embed = {
            "title": "📚 Skills tracker online",
            "description": (
                f"Now watching **{len(inv)}** skills across the fleet. New ones land "
                f"here as the bots pick them up, with the occasional 💡 spotlight on an "
                f"existing favourite.\n\n_Currently tracked:_ {clip(names, 800)}"
            ),
            "color": COLOR_INIT,
        }
        print(f"[skills-discord] first run — seeding {len(inv)} skills, posting intro")
        if webhook or dry:
            post_embeds(webhook or "", [embed], dry=dry)
        return

    if not fresh:
        print("[skills-discord] no new skills")
        return

    print(
        f"[skills-discord] posting {len(fresh)} new skill(s): {', '.join(s.name for s in fresh)}"
    )
    if webhook or dry:
        post_embeds(
            webhook or "",
            [new_skill_embed(s) for s in sorted(fresh, key=lambda s: s.name)],
            dry=dry,
        )


def run_spotlight(
    inv: dict[str, Skill], state: dict, webhook: str | None, *, dry: bool
) -> None:
    if not inv:
        print("[skills-discord] nothing to spotlight (empty inventory)")
        return

    recent: list[str] = state.get("spotlight_recent", [])
    # Prefer skills not spotlighted within the cooldown window; fall back to all.
    pool = [k for k in inv if k not in recent] or list(inv)

    # Deterministic, dependency-free rotation: advance a counter and index into a
    # stable ordering. Avoids Math.random-style nondeterminism in tests.
    idx = state.get("spotlight_counter", 0)
    pool.sort()
    chosen_key = pool[idx % len(pool)]
    chosen = inv[chosen_key]

    state["spotlight_counter"] = idx + 1
    recent = ([chosen_key] + recent)[:SPOTLIGHT_COOLDOWN]
    state["spotlight_recent"] = recent
    if not dry:
        save_state(state)

    print(f"[skills-discord] spotlight: {chosen.name} ({chosen.source})")
    if webhook or dry:
        post_embeds(webhook or "", [spotlight_embed(chosen)], dry=dry)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--spotlight",
        action="store_true",
        help="post one 💡 spotlight on an existing skill",
    )
    ap.add_argument(
        "--dry",
        action="store_true",
        help="print what would be posted; no Discord write, no state write",
    )
    args = ap.parse_args()

    webhook = get_webhook_url()
    if not webhook and not args.dry:
        print(
            "[skills-discord] no DISCORD_WEBHOOK_SKILLS / DISCORD_WEBHOOK_URL found",
            file=sys.stderr,
        )
        sys.exit(1)

    inv = discover_skills()
    if not inv:
        print(
            f"[skills-discord] no skills found under {GLOBAL_DIR} or {PLUGINS_DIR}/cache",
            file=sys.stderr,
        )
        sys.exit(1)

    state = load_state()
    if args.spotlight:
        run_spotlight(inv, state, webhook, dry=args.dry)
    else:
        run_new(inv, state, webhook, dry=args.dry)


if __name__ == "__main__":
    main()
