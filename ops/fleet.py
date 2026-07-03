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
  fleet session create <name> [opts]      wire up a NEW channel session (dir + access.json + roster row)
                                          opts: --cwd DIR --model MODEL --effort low|medium|high --emoji E
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
# Production defaults are unchanged; the two env overrides exist ONLY so a hermetic test can point
# the roster script + channel-state tree at a tmp dir (see ops/tests/test_fleet.py). Unset in prod.
ENSURE = Path(os.environ.get("FLEET_ENSURE_FILE") or HOME / ".local/share/claude-channels/ensure-sessions.sh")
CHANNELS = Path(os.environ.get("CLAUDE_CHANNELS_DIR") or HOME / ".claude/channels")
SKILLS_GLOBAL = HOME / ".claude/skills"

# Tommy's Discord user id — the sole allowlisted sender a fresh session trusts (per-channel bots
# start locked to him; widen later via `fleet alias`/manual access.json edits).
TOMMY_DISCORD_ID = "1382748563355734127"


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


def _assoc(name: str, text: str) -> dict[str, str]:
    """{session: value} from an assoc array literal NAME=( a x b y )."""
    m = re.search(rf"{name}=\(([^)]*)\)", text)
    if not m:
        return {}
    toks = m.group(1).split()
    return dict(zip(toks[::2], toks[1::2], strict=False))


def models(text: str | None = None) -> dict[str, str]:
    """{session: model} from SESSION_MODEL=( a x b y )."""
    return _assoc("SESSION_MODEL", text if text is not None else _ensure_text())


def effort(text: str | None = None) -> dict[str, str]:
    """{session: effort} from SESSION_EFFORT=( a x b y )."""
    return _assoc("SESSION_EFFORT", text if text is not None else _ensure_text())


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
def _expand_state(state: str) -> Path:
    """Resolve a DISCORD_STATE_DIR string (may hold $HOME / ~) to a real path."""
    return Path(os.path.expanduser(str(state).replace("$HOME", str(HOME))))


def _access_path(name: str, state: str | None = None) -> Path:
    # `create` passes the state dir explicitly (no roster row yet); everyone else derives it.
    st = state if state is not None else _find(name)["state"]
    return _expand_state(st) / "access.json"


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


def _new_access(name: str, chat_id: str | None, emoji: str | None) -> dict:
    """A locked-down access.json for a brand-new session, mirroring an existing one's shape
    (dmPolicy / allowFrom / groups / pending / mentionPatterns / ackReaction). Tommy is the sole
    allowFrom; if we already know the channel id it's pre-seeded as a require-mention group."""
    groups = {}
    if chat_id:
        groups[chat_id] = {"requireMention": True, "allowFrom": [TOMMY_DISCORD_ID]}
    return {
        "dmPolicy": "allowlist",
        "allowFrom": [TOMMY_DISCORD_ID],
        "groups": groups,
        "pending": {},
        # @name / @<name>bot so the bot answers to its own name in-channel.
        "mentionPatterns": [name, f"{name}bot"],
        "ackReaction": emoji or "",
    }


def _repo_channel_dir(name: str) -> Path:
    """channels/<name>/ in the current repo checkout (cwd = the mini repo when run via `just fleet`)."""
    return Path.cwd() / "channels" / name


def _deploy_workspace(name: str, workspace: Path) -> str:
    """Seed the session workspace from the repo's channels/<name>/ tracked files, honouring its
    .gitignore. Returns a human note for the summary. No-op (with a note) if the dir is absent."""
    src = _repo_channel_dir(name)
    if not src.is_dir():
        return f"no channels/{name}/ in the repo — workspace left empty (add sources later, then re-deploy)"
    cmd = ["rsync", "-a"]
    gi = src / ".gitignore"
    if gi.is_file():
        cmd += ["--exclude-from", str(gi)]
    cmd += ["--exclude", ".git", f"{src}/", f"{workspace}/"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return f"workspace deploy FAILED (rsync: {r.stderr.strip()[:120]}) — copy channels/{name}/ in by hand"
    return f"deployed channels/{name}/ → {workspace} (respecting .gitignore)"


def cmd_session_create(a) -> None:
    name = a.name
    text = _ensure_text()

    # 1. Guard idempotently — never partially wire an already-known session.
    if any(p["name"] == name for p in projects(text)):
        sys.exit(f"fleet: session '{name}' already exists in DISCORD_PROJECTS — nothing to do")
    if "DISCORD_PROJECTS=(" not in text:
        sys.exit("fleet: DISCORD_PROJECTS array not found in ensure-sessions.sh — aborted")

    state_dir = CHANNELS / f"discord-{name}"
    workspace = Path(a.cwd) if a.cwd else state_dir / "workspace"

    # 2. Scaffold the channel state dir + workspace. chmod explicitly (mkdir's mode is umask-masked,
    #    and a no-op on an already-existing dir) so the private state tree is 700 regardless.
    state_dir.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    if not a.cwd:  # only lock down the dirs we own; a caller-supplied --cwd is left as-is
        state_dir.chmod(0o700)
        workspace.chmod(0o700)

    # 3. Write access.json (reuse _access_path with the explicit state dir — no roster row yet).
    ap = _access_path(name, str(state_dir))
    ap.write_text(json.dumps(_new_access(name, chat_id=None, emoji=a.emoji), indent=2))

    # 4. Roster row + optional model/effort pins — one format-preserving, syntax-checked write.
    #    Row uses $HOME/… (matches the file's own dev-dev/home entries) unless --cwd is absolute.
    if a.cwd:
        cwd_field = a.cwd
    else:
        cwd_field = f"$HOME/.claude/channels/discord-{name}/workspace"
    state_field = f"$HOME/.claude/channels/discord-{name}"
    row = f'  "{name}|{cwd_field}|{state_field}"\n'
    new_text = re.sub(
        r"(DISCORD_PROJECTS=\(\s*\n.*?)(\n\)\n)",
        lambda m: m.group(1) + "\n" + row.rstrip("\n") + m.group(2),
        text,
        count=1,
        flags=re.DOTALL,
    )
    if new_text == text:
        sys.exit("fleet: couldn't locate the DISCORD_PROJECTS closing paren to append the row — aborted")
    if a.model:
        new_text = _set_assoc_text(new_text, "SESSION_MODEL", name, a.model)
    if a.effort:
        new_text = _set_assoc_text(new_text, "SESSION_EFFORT", name, a.effort)
    _write_ensure(new_text, f"create-{name}")

    # 5. Deploy the workspace from the repo (if channels/<name>/ exists).
    deploy_note = _deploy_workspace(name, workspace)
    if not a.cwd:  # rsync -a can carry the source dir's mode in — re-lock the private tree we own
        state_dir.chmod(0o700)
        workspace.chmod(0o700)

    # 6. Summary + the one irreducibly-manual step (Discord app creation is a UI action).
    env_path = state_dir / ".env"
    pins = []
    if a.model:
        pins.append(f"model={a.model}")
    if a.effort:
        pins.append(f"effort={a.effort}")
    print(f"\nfleet: session '{name}' wired up:")
    print(f"  • state dir   {state_dir}")
    print(f"  • workspace   {workspace}")
    print(f"  • access.json {ap}  (allowFrom Tommy; ackReaction={a.emoji or '—'})")
    print(f"  • roster row  \"{name}|{cwd_field}|{state_field}\"" + (f"  [{', '.join(pins)}]" if pins else ""))
    print(f"  • workspace   {deploy_note}")
    print("\n  MANUAL (can't be automated — Discord UI): create a Discord app + bot, invite it to")
    print(f"  #{name}, and save its token to {env_path} as")
    print("      DISCORD_BOT_TOKEN=…    (chmod 600)")
    print(f"  Then the babysitter starts the session within ~2 min, or run `just fleet session restart {name}`.")


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


def _set_assoc_text(text: str, array: str, name: str, value: str | None) -> str:
    """Return `text` with `name`→`value` set (or removed when value is falsy) inside the
    zsh assoc array `array=( … )`. Format-preserving, single-line rewrite. Requires the array
    to already exist in the file."""
    m = re.search(rf"{array}=\(([^)]*)\)", text)
    if not m:
        sys.exit(f"fleet: {array} array not found in ensure-sessions.sh")
    cur = _assoc(array, text)
    if value:
        cur[name] = value
    else:
        cur.pop(name, None)
    body = " ".join(f"{k} {v}" for k, v in cur.items())
    return text.replace(m.group(0), f"{array}=( {body} )" if body else f"{array}=( )", 1)


def cmd_session_set_model(a) -> None:
    _find(a.name)
    val = None if a.model in ("-", "default", "") else a.model
    _write_ensure(
        _set_assoc_text(_ensure_text(), "SESSION_MODEL", a.name, val), f"model-{a.name}"
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
    sc = s.add_parser("create", help="wire up a new channel session (dir + access.json + roster row)")
    sc.add_argument("name")
    sc.add_argument("--cwd", help="working dir (default: <state>/workspace)")
    sc.add_argument("--model", help="pin SESSION_MODEL (e.g. claude-sonnet-5)")
    sc.add_argument("--effort", choices=("low", "medium", "high"), help="pin SESSION_EFFORT")
    sc.add_argument("--emoji", help="ackReaction emoji for the channel")
    sc.set_defaults(fn=cmd_session_create)
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
