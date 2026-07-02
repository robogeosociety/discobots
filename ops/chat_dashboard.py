#!/usr/bin/env python3
"""chat_dashboard — tommybot's in-flight answer, live in #ops.

Consumes the tommybot#72 live-telemetry contract (docs/live.md): every request
atomically rewrites one JSON snapshot (TOMMYBOT_LIVE_FILE) with the sequenced
stages — ack → agent → db → model → ticks-while-thinking → done. This panel
polls that file and renders the response AS IT THINKS: stage dots advance, the
token counter climbs, the answer tail scrolls — one Discord message,
PATCH-edited (discokit.Dashboard). Between requests the panel is a static
"answered <t:R>" summary, so an idle poll makes no request at all.

The live container already mounts ~/Library/Caches/tommybot read-only for the
embed dashboard, so the default live-file path needs no new mount — tommybot's
side just sets TOMMYBOT_LIVE_FILE=<cache>/live.json in its .env (see live.md).

    # see the whole feel locally, no file, no Discord, no deps:
    python3 ops/chat_dashboard.py --dry --demo

    # run live (inside the discobot-live inner loop; standalone = rollback):
    python3 ops/chat_dashboard.py --interval 5 --iterations 0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# discokit (the package) sits next to this file, in ops/ — and flat in /app
# inside the container. Put that dir on the path either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit import config, tokens  # noqa: E402
from discokit.dashboard import Dashboard  # noqa: E402
from discokit.live import Job  # noqa: E402
from discokit.poster import Poster  # noqa: E402

STALE_AFTER_S = 20  # done:false + no rewrite in ≫ TOMMYBOT_TICK ⇒ died mid-request
_SPINNER = "⠋⠙⠸⠴⠦⠇"  # the current stage's dot rotates one frame per render
TAIL_CHARS = 160
ANSWER_CHARS = 420
TITLE = "💬 tommybot · live answer"


# --- reading the live file -------------------------------------------------
def read_live(path: Path) -> tuple[dict | None, float]:
    """The snapshot + its mtime. Atomic writer (tmp+rename) ⇒ never torn."""
    try:
        return json.loads(path.read_text()), path.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return None, 0.0


# --- rendering ---------------------------------------------------------------
def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else "…" + text[-(limit - 1) :]


def _code_safe(text: str) -> str:
    return (text or "").replace("```", "'''")


def _stage(live: dict, name: str) -> dict | None:
    for s in live.get("stages", []):
        if s.get("stage") == name:
            return s
    return None


def _stage_dots(live: dict) -> str:
    """The sequence as dots: reached stages 🟢, the running one spins."""
    reached = {s.get("stage") for s in live.get("stages", [])}
    phase = live.get("phase")
    spin = _SPINNER[int(live.get("t") or 0) % len(_SPINNER)]
    parts = []
    for name in ("ack", "agent", "db", "model"):
        if name == phase and not live.get("done"):
            parts.append(f"{spin} {name}")
        elif name in reached:
            label = name
            if name == "db" and (db := _stage(live, "db")):
                label = f"db {db.get('hits', '?')} hits"
            parts.append(f"{tokens.OPERATIONAL.dot} {label}")
        else:
            parts.append(f"{tokens.UNKNOWN.dot} {name}")
    return " · ".join(parts)


def build_panel(live: dict | None, *, now: float, mtime: float) -> dict:
    """Build the dashboard embed ({"embeds":[…]}) — no freshness stamp."""
    if live is None:
        st = tokens.UNKNOWN
        body = (
            f"{st.glyph} **no live telemetry** — tommybot hasn't written the live file yet\n"
            f"_(set `TOMMYBOT_LIVE_FILE` in tommybot's env — tommybot docs/live.md)_"
        )
        return {"embeds": [{"title": TITLE, "description": body, "color": st.color}]}

    agent = (_stage(live, "agent") or {}).get("name") or "?"
    vault = live.get("vault") or "?"
    query = _clip(live.get("query") or "", 120)

    if live.get("error") or _stage(live, "error"):
        st = tokens.CRITICAL
        err = _clip(str(live.get("error") or (_stage(live, "error") or {}).get("error", "")), 200)
        body = f"{st.glyph} **failed** · {vault} · {agent}\n> {query}\n`{err}`"
        return {"embeds": [{"title": TITLE, "description": body, "color": st.color}]}

    if live.get("done"):
        st = tokens.OPERATIONAL
        stats = live.get("stats") or {}
        done = _stage(live, "done") or {}
        toks = stats.get("output_tokens") or done.get("tokens_out") or "?"
        dur = done.get("duration_s") or live.get("t") or 0
        nsrc = len(live.get("sources") or [])
        started = live.get("started", "")
        epoch = _iso_epoch(started)
        stamp = f" <t:{epoch}:R>" if epoch else ""
        answer = _code_safe(_clip(live.get("answer") or "", ANSWER_CHARS))
        body = (
            f"{st.glyph} **answered**{stamp} · {vault} · {agent} · {nsrc} sources\n"
            f"> {query}\n"
            f"```text\n{answer}\n```\n"
            f"{toks} tok · {dur:.1f}s"
        )
        return {"embeds": [{"title": TITLE, "description": body, "color": st.color}]}

    if now - mtime > STALE_AFTER_S:
        st = tokens.DEGRADED
        body = (
            f"{st.glyph} **died mid-request** — live file stopped moving\n"
            f"> {query}\n"
            f"last phase `{live.get('phase')}` · t={live.get('t', 0):.0f}s"
        )
        return {"embeds": [{"title": TITLE, "description": body, "color": st.color}]}

    # in flight — this is the panel that visibly thinks
    st = tokens.INFO
    tick = live.get("tick") or {}
    toks = tick.get("tokens")
    tail = _code_safe(_clip(tick.get("answer_tail") or "", TAIL_CHARS))
    lines = [
        f"{st.glyph} **thinking** · {vault} · {agent}",
        _stage_dots(live),
        f"> {query}",
    ]
    if tail:
        lines.append(f"`{tail}`")
    meter = f"{live.get('t', 0):.0f}s"
    if toks is not None:
        meter = f"**{toks} tok** · " + meter
    lines.append(meter)
    return {"embeds": [{"title": TITLE, "description": "\n".join(lines), "color": st.color}]}


def _iso_epoch(iso: str) -> int | None:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except (ValueError, TypeError):
        return None


# --- the job (shared by the standalone daemon and live_service's inner loop) --
def make_job(
    url: str | None,
    *,
    dry: bool = False,
    state: str,
    interval: float = 5,
    live_file: str = "/mnt/tommybot-cache/live.json",
) -> Job:
    """One live-file poll as a live.Job — read, rebuild the panel, reconcile."""
    dash = Dashboard(Poster(url, dry=dry), state_path=state, key="chat", source="chat-dashboard")
    path = Path(live_file)

    def tick() -> str:
        live, mtime = read_live(path)
        return dash.tick(build_panel(live, now=time.time(), mtime=mtime))

    return Job("chat", interval, tick)


# --- demo: a scripted request so `--dry --demo` shows the panel thinking ------
def _demo_sequence() -> list[tuple[dict | None, float]]:
    now = time.time()
    base = {
        "query": "what does the 2p tent weigh with stakes?",
        "vault": "gear",
        "started": "2026-07-01T21:04:05+00:00",
        "error": None, "answer": None, "sources": None, "stats": None,
    }
    stages = [
        {"stage": "ack", "seq": 0, "t": 0.0},
        {"stage": "agent", "seq": 1, "t": 0.01, "name": "Gearhead"},
        {"stage": "db", "seq": 2, "t": 0.42, "hits": 5, "retrieval": "hybrid"},
        {"stage": "model", "seq": 3, "t": 0.43, "model": "mlx-community/Qwen3-4B-4bit", "warm": True},
    ]
    thinking1 = {**base, "t": 2.0, "phase": "model", "done": False, "stages": stages[:4],
                 "tick": {"t": 2.0, "phase": "model", "tokens": 18, "answer_tail": "…the Copper Spur"}}
    thinking2 = {**base, "t": 7.0, "phase": "model", "done": False, "stages": stages[:4],
                 "tick": {"t": 7.0, "phase": "model", "tokens": 74,
                          "answer_tail": "…weighs 1.36 kg packed; add 210 g for"}}
    done = {**base, "t": 11.8, "phase": "done", "done": True,
            "stages": stages + [{"stage": "done", "seq": 4, "t": 11.8, "tokens_out": 132, "duration_s": 11.8}],
            "answer": "The Copper Spur HV UL2 weighs 1.36 kg packed. With the 8 stock stakes "
                      "(210 g) and the footprint (198 g) you're at 1.77 kg trail weight.",
            "sources": [{"title": "gear/Tent"}, {"title": "gear/Stakes"}],
            "stats": {"output_tokens": 132}}
    return [
        (None, 0.0),                       # no live file yet
        ({**base, "t": 0.4, "phase": "db", "done": False, "stages": stages[:3]}, now),
        (thinking1, now),
        (thinking2, now),
        (done, now),
        (done, now),                       # idle after — must render unchanged
        ({**thinking2, "t": 9.0}, now - 120),  # stalled writer → degraded
    ]


DEMO_CAPTION = ["no file", "retrieving", "thinking (18 tok)", "thinking (74 tok)",
                "answered", "idle (no change)", "died mid-request"]


# --- loop ----------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="the #ops tommybot live-answer panel (discokit)")
    ap.add_argument("--dry", action="store_true", help="print create/edit calls, post nothing")
    ap.add_argument("--demo", action="store_true", help="replay a scripted request sequence")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between demo ticks")
    ap.add_argument("--interval", type=int, default=5, help="poll seconds (live mode)")
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever (live mode)")
    ap.add_argument("--state", default=os.environ.get("CHAT_DASH_STATE", "/tmp/chat-dashboard.json"))
    ap.add_argument("--live-file", default=os.environ.get("TOMMYBOT_LIVE_FILE", "/mnt/tommybot-cache/live.json"))
    args = ap.parse_args()

    url = config.webhook("OPS")
    if not args.dry and not url:
        print("[chat_dashboard] no DISCORD_WEBHOOK_OPS / DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    run = "DEMO" if args.demo else args.live_file
    print(f"[*] chat panel — {run}{' · DRY' if args.dry else ''} · state={args.state}")

    if args.demo:
        dash = Dashboard(Poster(url, dry=args.dry), state_path=args.state, key="chat", source="chat-dashboard")
        now = time.time()
        for i, (live, mtime) in enumerate(_demo_sequence()):
            print(f"\ntick {i}  ({DEMO_CAPTION[i]})")
            print(f"  └─ → {dash.tick(build_panel(live, now=now, mtime=mtime))}")
            if args.delay and i < len(DEMO_CAPTION) - 1:
                time.sleep(args.delay)
        print("\n[done] one message, edited in place — no reposts.")
        return

    job = make_job(url, dry=args.dry, state=args.state, interval=args.interval, live_file=args.live_file)
    tick = 0
    while args.iterations == 0 or tick < args.iterations:
        print(f"[tick {tick}] {job.tick()}")
        tick += 1
        if args.iterations == 0 or tick < args.iterations:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
