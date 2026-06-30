#!/usr/bin/env python3
"""fleet — manage the Claude Code Discord channel-session fleet (local config, mini-side).

The fleet = one ``claude --channels`` session per Discord channel, kept alive by the launchd
babysitter. This is the local control plane for it; the Discord-web side (app name/icon, server
emoji, roles/perms) is a separate Playwright-driven tool.

Operates on:
  ~/.local/share/claude-channels/ensure-sessions.sh   — DISCORD_PROJECTS (name|cwd|state) + SESSION_MODEL
  ~/.claude/channels/discord-<name>/access.json        — alias (mentionPatterns) + emoji (ackReaction)
  skills: ~/.claude/skills (global, all sessions) + <cwd>/.claude/skills (per-session)

  fleet ls                                overview of every session
  fleet session set-cwd <name> <dir>      repoint a session's working dir   (backup + zsh -n)
  fleet session set-model <name> <model>  pin/unpin a session's model       (backup + zsh -n; '-' clears)
  fleet session restart <name>            restart it (nomad restart-maclaude, else tmux kill → babysitter)
  fleet skill ls [<name>]                 skills a session sees (global + its cwd/.claude/skills)
  fleet skill link|unlink <name> <skill>  symlink a global skill into / out of a session's cwd/.claude/skills
  fleet emoji set <name> <emoji>          set the channel ack emoji (access.json ackReaction)
  fleet alias set <name> <pattern...>     set @mention aliases (access.json mentionPatterns)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
ENSURE = HOME / ".local/share/claude-channels/ensure-sessions.sh"
CHANNELS = HOME / ".claude/channels"
SKILLS_GLOBAL = HOME / ".claude/skills"


# ── parse ensure-sessions.sh ─────────────────────────────────────────────────────
def _ensure_text() -> str:
    return ENSURE.read_text()


def projects(text: str | None = None) -> list[dict]:
    """[{name, cwd, state}] from the DISCORD_PROJECTS array."""
    text = text if text is not None else _ensure_text()
    m = re.search(r"DISCORD_PROJECTS=\(\s*\n(.*?)\n\)", text, re.DOTALL)
    out: list[dict] = []
    if not m:
        return out
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        em = re.match(r'"([^|]+)\|([^|]+)\|([^"]+)"', line)
        if em:
            out.append({"name": em.group(1), "cwd": em.group(2), "state": em.group(3)})
    return out


def models(text: str | None = None) -> dict[str, str]:
    """{session: model} from SESSION_MODEL=( a x b y )."""
    text = text if text is not None else _ensure_text()
    m = re.search(r"SESSION_MODEL=\(([^)]*)\)", text)
    if not m:
        return {}
    toks = m.group(1).split()
    return dict(zip(toks[::2], toks[1::2], strict=False))


def _find(name: str) -> dict:
    for p in projects():
        if p["name"] == name:
            return p
    sys.exit(f"fleet: unknown session '{name}' (try `fleet ls`)")


# ── safe edits to ensure-sessions.sh ─────────────────────────────────────────────
def _write_ensure(new_text: str, tag: str) -> None:
    bak = ENSURE.with_name(f"{ENSURE.name}.bak-{tag}-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(ENSURE, bak)
    tmp = ENSURE.with_suffix(".tmp")
    tmp.write_text(new_text)
    if subprocess.run(["zsh", "-n", str(tmp)]).returncode != 0:
        tmp.unlink()
        sys.exit(
            f"fleet: edit would break ensure-sessions.sh syntax — aborted (backup {bak.name})"
        )
    tmp.replace(ENSURE)
    print(
        f"fleet: updated ensure-sessions.sh (backup {bak.name}). Restart the session to apply."
    )


# ── per-channel access.json (alias / emoji) ──────────────────────────────────────
def _access_path(name: str) -> Path:
    state = Path(_find(name)["state"])  # DISCORD_STATE_DIR; honour ~ etc.
    return (
        Path(os.path.expanduser(str(state).replace("$HOME", str(HOME)))) / "access.json"
    )


def _edit_access(name: str, key: str, value) -> None:
    p = _access_path(name)
    d = json.loads(p.read_text())
    d[key] = value
    bak = p.with_name(f"access.json.bak-{key}-{datetime.now():%Y%m%d%H%M%S}")
    shutil.copy2(p, bak)
    p.write_text(json.dumps(d, indent=2))
    print(
        f"fleet: {name} {key} = {value!r} (backup {bak.name}). Restart the session to apply."
    )


def _access_get(name: str, key: str, default=None):
    try:
        return json.loads(_access_path(name).read_text()).get(key, default)
    except (OSError, json.JSONDecodeError):
        return default


# ── skills ────────────────────────────────────────────────────────────────────
def _skill_names(d: Path) -> list[str]:
    return sorted(s.parent.name for s in d.glob("*/SKILL.md")) if d.is_dir() else []


def _session_skill_dir(name: str) -> Path:
    return Path(os.path.expanduser(_find(name)["cwd"])) / ".claude" / "skills"


# ── commands ────────────────────────────────────────────────────────────────────
def cmd_ls(_a) -> None:
    md = models()
    glob_n = len(_skill_names(SKILLS_GLOBAL))
    print(f"{'session':<10} {'model':<8} {'emoji':<6} {'live':<5} {'+skills':<8} cwd")
    for p in projects():
        n = p["name"]
        live = (
            "yes"
            if subprocess.run(
                ["tmux", "has-session", "-t", n], capture_output=True
            ).returncode
            == 0
            else "—"
        )
        local = len(_skill_names(_session_skill_dir(n)))
        print(
            f"{n:<10} {md.get(n, '(default)'):<8} {str(_access_get(n, 'ackReaction', '—')):<6} "
            f"{live:<5} {f'{glob_n}+{local}':<8} {p['cwd']}"
        )


def cmd_session_set_cwd(a) -> None:
    p = _find(a.name)
    text = _ensure_text()
    old = f'"{p["name"]}|{p["cwd"]}|{p["state"]}"'
    new = f'"{p["name"]}|{a.dir}|{p["state"]}"'
    if old not in text:
        sys.exit(
            "fleet: couldn't locate the session's DISCORD_PROJECTS line verbatim — aborted"
        )
    _write_ensure(text.replace(old, new, 1), f"cwd-{a.name}")


def cmd_session_set_model(a) -> None:
    _find(a.name)
    text = _ensure_text()
    m = re.search(r"SESSION_MODEL=\(([^)]*)\)", text)
    if not m:
        sys.exit("fleet: SESSION_MODEL array not found")
    cur = models(text)
    if a.model in ("-", "default", ""):
        cur.pop(a.name, None)
    else:
        cur[a.name] = a.model
    body = " ".join(f"{k} {v}" for k, v in cur.items())
    _write_ensure(
        text.replace(
            m.group(0), f"SESSION_MODEL=( {body} )" if body else "SESSION_MODEL=( )", 1
        ),
        f"model-{a.name}",
    )


def cmd_session_restart(a) -> None:
    _find(a.name)
    disp = subprocess.run(
        ["nomad", "job", "dispatch", "-meta", f"session={a.name}", "restart-maclaude"],
        capture_output=True,
        text=True,
    )
    if disp.returncode == 0:
        print(f"fleet: dispatched restart-maclaude for '{a.name}' — respawns in ~30s.")
        return
    print(
        f"fleet: nomad restart unavailable ({disp.stderr.strip()[:80]}); killing tmux session — babysitter respawns ≤2m."
    )
    subprocess.run(["tmux", "kill-session", "-t", a.name])
    print(f"fleet: killed tmux '{a.name}'.")


def cmd_skill_ls(a) -> None:
    glob = _skill_names(SKILLS_GLOBAL)
    print(f"global ({len(glob)}): {', '.join(glob) or '—'}")
    names = [a.name] if a.name else [p["name"] for p in projects()]
    for n in names:
        local = _skill_names(_session_skill_dir(n))
        print(
            f"  {n}: {', '.join(local) if local else '(no project skills)'}  [{_session_skill_dir(n)}]"
        )


def cmd_skill_link(a) -> None:
    src = SKILLS_GLOBAL / a.skill
    if not (src / "SKILL.md").is_file():
        sys.exit(f"fleet: no global skill '{a.skill}' in {SKILLS_GLOBAL}")
    dst_dir = _session_skill_dir(a.name)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / a.skill
    if dst.exists() or dst.is_symlink():
        sys.exit(f"fleet: {dst} already exists")
    dst.symlink_to(src)
    print(f"fleet: linked {a.skill} → {dst} (restart the session to load it)")


def cmd_skill_unlink(a) -> None:
    dst = _session_skill_dir(a.name) / a.skill
    if not (dst.is_symlink() or dst.exists()):
        sys.exit(f"fleet: {dst} not present")
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    else:
        shutil.rmtree(dst)
    print(f"fleet: unlinked {a.skill} from {a.name} (restart the session to apply)")


def cmd_emoji_set(a) -> None:
    _edit_access(a.name, "ackReaction", a.emoji)


def cmd_alias_set(a) -> None:
    _edit_access(a.name, "mentionPatterns", list(a.pattern))


def _arg(parser, *names, **kw):
    """Add positionals + a default fn, then return the parser — keeps the wiring terse but lint-clean."""
    fn = kw.pop("fn")
    for n in names:
        parser.add_argument(
            n,
            **({"nargs": kw.pop("nargs")} if n == names[-1] and "nargs" in kw else {}),
        )
    parser.set_defaults(fn=fn)
    return parser


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="fleet",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ls").set_defaults(fn=cmd_ls)

    s = sub.add_parser("session").add_subparsers(dest="op", required=True)
    _arg(s.add_parser("set-cwd"), "name", "dir", fn=cmd_session_set_cwd)
    _arg(s.add_parser("set-model"), "name", "model", fn=cmd_session_set_model)
    _arg(s.add_parser("restart"), "name", fn=cmd_session_restart)

    k = sub.add_parser("skill").add_subparsers(dest="op", required=True)
    _arg(k.add_parser("ls"), "name", nargs="?", fn=cmd_skill_ls)
    _arg(k.add_parser("link"), "name", "skill", fn=cmd_skill_link)
    _arg(k.add_parser("unlink"), "name", "skill", fn=cmd_skill_unlink)

    e = sub.add_parser("emoji").add_subparsers(dest="op", required=True)
    _arg(e.add_parser("set"), "name", "emoji", fn=cmd_emoji_set)

    al = sub.add_parser("alias").add_subparsers(dest="op", required=True)
    _arg(al.add_parser("set"), "name", "pattern", nargs="*", fn=cmd_alias_set)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
