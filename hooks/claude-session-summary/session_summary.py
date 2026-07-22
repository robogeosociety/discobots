#!/usr/bin/env python3
"""Post a Claude Code session summary to #dev.

Runs in two modes:
  session_summary.py hook           read SessionEnd hook JSON on stdin, summarize that session
  session_summary.py sweep          scan all local transcripts for idle, unposted, substantial
                                    sessions and post each (the daily backstop for sessions
                                    that never fired SessionEnd)
  session_summary.py post <jsonl>   force-summarize one transcript (testing)
  session_summary.py cloud-hook     SessionEnd entry point for the repo-committed hook used
                                    by Claude Code web sessions. No-ops on machines that run
                                    the installed user-level pipeline (dev-summary.env
                                    present), so Air/mini never double-post; in the cloud
                                    sandbox it reads DISCORD_WEBHOOK_DEV from the
                                    environment config and falls back to a plain digest if
                                    the claude CLI is absent.

Config (env or ~/.claude/hooks/dev-summary.env):
  DISCORD_WEBHOOK_DEV   required — the #dev webhook
  DEV_SUMMARY_MODEL     summarizer model (default claude-haiku-4-5-20251001)
  DEV_SUMMARY_SKIP=1    set inside the summarizer's own claude -p call to prevent recursion

State: ~/.claude/hooks/dev-summary-posted.json — session ids already posted, shared by
hook and sweep so a session is never posted twice. Stdlib only; no third-party deps.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
HOOKS_DIR = CLAUDE_DIR / "hooks"
STATE = HOOKS_DIR / "dev-summary-posted.json"
ENV_FILE = HOOKS_DIR / "dev-summary.env"

MIN_USER_MSGS = 1          # substantiality gate: at least one human prompt...
MIN_ASSISTANT_MSGS = 6     # ...and enough assistant turns to be real work
SWEEP_IDLE_MIN = 45        # sweep: session considered finished after this much idle
SWEEP_MAX_AGE_DAYS = 3     # sweep: don't resurrect ancient sessions
TRANSCRIPT_CAP = 60_000    # chars of condensed transcript fed to the summarizer
DISCORD_LIMIT = 1900


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)


def load_state():
    try:
        return set(json.loads(STATE.read_text()))
    except Exception:
        return set()


def save_state(posted):
    HOOKS_DIR.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(sorted(posted)))


def condense(path: Path):
    """Return (meta, condensed_text) for a transcript, or None if not substantial."""
    title, cwd, model = None, None, None
    first_ts = last_ts = None
    user_n = asst_n = 0
    parts = []
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            o = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        t = o.get("type")
        if t == "ai-title":
            title = o.get("aiTitle") or title
            continue
        if t not in ("user", "assistant") or o.get("isSidechain"):
            continue
        ts = o.get("timestamp")
        if ts:
            first_ts = first_ts or ts
            last_ts = ts
        cwd = o.get("cwd") or cwd
        msg = o.get("message") or {}
        content = msg.get("content")
        if t == "user":
            if isinstance(content, str) and content.strip():
                if content.startswith("<") or o.get("toolUseResult") is not None:
                    continue
                user_n += 1
                parts.append(f"USER: {content.strip()[:2000]}")
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                text = "\n".join(x for x in texts if x.strip())
                if text.strip() and not text.lstrip().startswith("<"):
                    user_n += 1
                    parts.append(f"USER: {text.strip()[:2000]}")
        else:
            model = msg.get("model") or model
            if isinstance(content, list):
                texts, tools = [], []
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        texts.append(b["text"].strip())
                    elif b.get("type") == "tool_use":
                        tools.append(b.get("name", "?"))
                if texts or tools:
                    asst_n += 1
                if tools:
                    parts.append(f"ASSISTANT tools: {', '.join(tools[:8])}")
                if texts:
                    parts.append(f"ASSISTANT: {' '.join(texts)[:3000]}")
    if user_n < MIN_USER_MSGS or asst_n < MIN_ASSISTANT_MSGS:
        return None
    text = "\n".join(parts)
    if len(text) > TRANSCRIPT_CAP:
        half = TRANSCRIPT_CAP // 2
        text = text[:half] + "\n[... middle elided ...]\n" + text[-half:]
    meta = {
        "title": title,
        "cwd": cwd,
        "model": model,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "user_n": user_n,
    }
    return meta, text


def find_claude():
    for p in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude")):
        if p.exists():
            return str(p)
    return "claude"


def summarize(meta, text):
    prompt = f"""Summarize this Claude Code session transcript as a single Discord message for the #dev channel.

Style — match this house style exactly:
- First line: one fitting emoji + **bold title** naming the work (use the session title if apt: {meta.get('title') or 'none'}).
- Then 2-5 tight lines: what was asked, what actually got done/decided, and concrete artifacts (repos, PRs, files, commands, hosts). Bold the key nouns.
- Plain confident prose, no headers, no bullet-list filler, no "the user"/"the assistant" — write it like a changelog entry from the team.
- If work is unfinished, say what's next in one line.
- HARD LIMIT {DISCORD_LIMIT} characters total. No preamble, output only the message.

Transcript (condensed):
{text}"""
    env = dict(os.environ, DEV_SUMMARY_SKIP="1")
    model = os.environ.get("DEV_SUMMARY_MODEL", "claude-haiku-4-5-20251001")
    r = subprocess.run(
        [find_claude(), "-p", "--model", model],
        input=prompt,
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        raise RuntimeError(f"claude -p failed: {r.stderr[:400]}")
    return out[:DISCORD_LIMIT]


def plain_digest(meta, text):
    first_user = next(
        (p[6:] for p in text.split("\n") if p.startswith("USER: ")), ""
    )
    title = meta.get("title") or first_user[:80] or "Claude session"
    lines = [f"\U0001f4dd **{title}**"]
    if first_user and first_user[:80] != title:
        lines.append(f"Asked: {first_user[:300]}")
    lines.append(
        f"{meta['user_n']} prompt(s), model {meta.get('model') or '?'} — "
        "no summarizer available in this sandbox, plain digest only."
    )
    return "\n".join(lines)[:DISCORD_LIMIT]


def duration_str(meta):
    try:
        a = datetime.fromisoformat(meta["first_ts"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(meta["last_ts"].replace("Z", "+00:00"))
        m = int((b - a).total_seconds() // 60)
        return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"
    except Exception:
        return "?"


def post(summary, meta):
    url = os.environ.get("DISCORD_WEBHOOK_DEV")
    if not url:
        raise RuntimeError("DISCORD_WEBHOOK_DEV not set")
    host = os.environ.get("_DEV_SUMMARY_HOST") or socket.gethostname().split(".")[0]
    cwd = re.sub(r"^/(Users|Volumes)/[^/]+/", "~/", meta.get("cwd") or "?")
    footer = f"\n-# claude session · {host} · {cwd} · {duration_str(meta)}"
    body = json.dumps(
        {"content": summary[:DISCORD_LIMIT] + footer, "username": "tommyroar"}
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-session-summary/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def handle(path: Path, session_id: str, posted):
    if session_id in posted:
        return False
    got = condense(path)
    if not got:
        return False
    meta, text = got
    summary = summarize(meta, text)
    post(summary, meta)
    posted.add(session_id)
    save_state(posted)
    return True


def cmd_hook():
    if os.environ.get("DEV_SUMMARY_SKIP"):
        return
    data = json.load(sys.stdin)
    tp = data.get("transcript_path")
    sid = data.get("session_id")
    if not tp or not sid or not Path(tp).exists():
        return
    handle(Path(tp), sid, load_state())


def cmd_cloud_hook():
    if ENV_FILE.exists():
        return  # this machine runs the installed user-level pipeline
    if os.environ.get("DEV_SUMMARY_SKIP") or not os.environ.get("DISCORD_WEBHOOK_DEV"):
        return
    data = json.load(sys.stdin)
    tp = data.get("transcript_path")
    sid = data.get("session_id")
    if not tp or not sid or not Path(tp).exists():
        return
    got = condense(Path(tp))
    if not got:
        return
    meta, text = got
    os.environ["_DEV_SUMMARY_HOST"] = "claude-web"
    try:
        summary = summarize(meta, text)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        summary = plain_digest(meta, text)
    post(summary, meta)


def cmd_sweep():
    posted = load_state()
    now = time.time()
    n = 0
    for path in (CLAUDE_DIR / "projects").glob("*/*.jsonl"):
        age = now - path.stat().st_mtime
        if age < SWEEP_IDLE_MIN * 60 or age > SWEEP_MAX_AGE_DAYS * 86400:
            continue
        sid = path.stem
        try:
            if handle(path, sid, posted):
                n += 1
                print(f"posted {sid}")
        except Exception as e:
            print(f"skip {sid}: {e}", file=sys.stderr)
    print(f"sweep done, {n} posted")


def main():
    load_env()
    mode = sys.argv[1] if len(sys.argv) > 1 else "hook"
    if mode == "hook":
        cmd_hook()
    elif mode == "cloud-hook":
        cmd_cloud_hook()
    elif mode == "sweep":
        cmd_sweep()
    elif mode == "post":
        p = Path(sys.argv[2])
        meta, text = condense(p) or (None, None)
        if not meta:
            sys.exit("not substantial")
        summary = summarize(meta, text)
        post(summary, meta)
        posted = load_state()
        posted.add(p.stem)
        save_state(posted)
        print(summary)
    else:
        sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"dev-summary error: {e}", file=sys.stderr)
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1] in ("hook", "cloud-hook") else 1)
