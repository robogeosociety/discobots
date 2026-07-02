#!/usr/bin/env python3
"""embed_dashboard — tommybot's slow embeddings trickle-sync graph, in #ops.

ONE self-editing Discord message that graphs tommybot's embedding sync (obsidian-automations'
`*/15` trickle → `tommybot reindex`, tommybot#57 — a deliberately SLOW, min-RAM rebuild: ≤48 chunks
per tick instead of the OOM-prone whole-corpus embed). Reads the embeddings DB directly — no
InfluxDB — since the trickle session itself emits no telemetry (only the Nomad wrapper's per-tick
success/duration does, in the separate `qwenbot_reindex` measurement); this bot is the sync's own
observability. Shows: a **growth sparkline** of total embedded chunks (self-tracked across polls,
so the slow climb is visible), the **per-vault** split, the **last sync** (timestamp + counts,
labeled like the loop wheel's last-tick), the embed model, and staleness (an unfolded WAL, or no
sync in a while, means the trickle isn't landing). Mirrors ops_dashboard.py / loop_dashboard.py —
posts once, PATCH-edits in place, only bumps on real change (discokit.Dashboard).

    # see the whole feel locally — no DB, no Discord, no deps:
    python3 ops/embed_dashboard.py --dry --demo

    # run live on the mini against the real #ops webhook (DB mounted read-only):
    python3 ops/embed_dashboard.py --interval 300 --iterations 0 --state /state/embed.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

# discokit (the package) sits next to this file, in ops/ — and flat in /app inside the
# container. Put that dir on the path either way (same shim as ops_dashboard.py).
_OPS = Path(__file__).resolve().parent
sys.path.insert(0, str(_OPS))

from discokit import config, graph, tokens  # noqa: E402
from discokit.dashboard import Dashboard  # noqa: E402
from discokit.live import Job  # noqa: E402
from discokit.poster import Poster  # noqa: E402

# The vaults tommybot embeds (mirrors tommybot.obsidian.VALID_VAULTS / obsidian-automations'
# qwenbot_reindex.VAULTS) — a fixed list so an untouched vault still renders as an empty bar,
# the visible signal that the trickle hasn't reached it yet.
VAULTS = ("camping", "dev", "gear", "home", "travel")

STALE_AFTER_S = 1800  # no sync in 30 min ⇒ the */15 trickle likely isn't landing
HISTORY_CAP = 96  # capped growth-history samples (≈ 8h at a 5 min poll)


# --- reading the tommybot embeddings DB (read-only; no writer risk) ------------------------
def _reindex_summary(raw: str | None) -> dict | None:
    """Parse a `last_reindex:<vault>` meta value — handles BOTH shapes that land there: the
    old whole-vault `reindex_vault` counts ({embedded, skipped, deleted}) and the new bounded
    `reindex_session` result ({session:True, embedded_chunks, changed, rolled, deleted, …})."""
    if not raw:
        return None
    try:
        d = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        return None
    if "embedded_chunks" in d:  # the new trickle-session shape
        return {
            "ts": d.get("ts"),
            "embedded": d.get("embedded_chunks", 0),
            "changed": d.get("changed"),
            "rolled": d.get("rolled"),
            "deleted": d.get("deleted", 0),
            "session": True,
        }
    return {
        "ts": d.get("ts"),
        "embedded": d.get("embedded", 0),
        "changed": None,
        "rolled": None,
        "deleted": d.get("deleted", 0),
        "session": False,
    }


def read_db(db_path: Path) -> dict | None:
    """Read-only snapshot of the embeddings DB, or None if it's missing/unreadable/empty.

    Opened with ``immutable=1`` — correct for a live WAL-mode DB mounted read-only: it skips the
    WAL entirely (no shared-memory writer access needed) and reads as of the last checkpoint. The
    trickle session folds its WAL after every tick, so this lags at most one in-flight session."""
    if not db_path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
        try:
            model = con.execute(
                "SELECT value FROM meta WHERE key='embedding_model_id'"
            ).fetchone()
            vaults: dict[str, dict] = {v: {"files": 0, "chunks": 0, "last": None} for v in VAULTS}
            for vault, files, chunks in con.execute(
                "SELECT vault, COUNT(DISTINCT path), COUNT(*) FROM chunks GROUP BY vault"
            ):
                vaults.setdefault(vault, {"files": 0, "chunks": 0, "last": None})
                vaults[vault]["files"] = files
                vaults[vault]["chunks"] = chunks
            for vault in VAULTS:
                row = con.execute(
                    "SELECT value FROM meta WHERE key=?", (f"last_reindex:{vault}",)
                ).fetchone()
                vaults[vault]["last"] = _reindex_summary(row[0] if row else None)
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return None

    wal = db_path.with_name(db_path.name + "-wal")
    total_chunks = sum(v["chunks"] for v in vaults.values())
    last_sync = max(
        (v["last"] for v in vaults.values() if v["last"] and v["last"].get("ts")),
        key=lambda s: s["ts"],
        default=None,
    )
    last_vault = next(
        (name for name, v in vaults.items() if v["last"] is last_sync), None
    )
    return {
        "ok": True,
        "model": model[0] if model else None,
        "vaults": vaults,
        "total_chunks": total_chunks,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "last_sync": last_sync,
        "last_vault": last_vault,
    }


# --- growth history (self-tracked — the trickle emits no time-series telemetry) -------------
def _history_path(state_path: str) -> Path:
    p = Path(state_path)
    return p.with_name(p.stem + ".history.json")


def load_history(state_path: str) -> list[int]:
    try:
        return json.loads(_history_path(state_path).read_text())
    except (OSError, ValueError):
        return []


def save_history(state_path: str, history: list[int]) -> None:
    hp = _history_path(state_path)
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text(json.dumps(history[-HISTORY_CAP:]))


def record(state_path: str, total_chunks: int) -> list[int]:
    """Append a sample if the total changed (a static wait between polls needn't pad the line)."""
    history = load_history(state_path)
    if not history or history[-1] != total_chunks:
        history.append(total_chunks)
        save_history(state_path, history)
    return history


# --- rendering -----------------------------------------------------------------------------
def _status(snap: dict | None) -> tokens.Status:
    if snap is None or not snap.get("ok"):
        return tokens.CRITICAL  # the DB itself is missing/unreadable
    last = snap.get("last_sync")
    if not last or not last.get("ts") or (time.time() - last["ts"]) > STALE_AFTER_S:
        return tokens.DEGRADED  # no sync recently — the */15 trickle likely isn't landing
    return tokens.INFO  # healthy monitor (informational, not an alert)


def build_panel(snap: dict | None, history: list[int]) -> dict:
    """Build the dashboard embed body ({"embeds":[…]}) — no freshness stamp (Dashboard owns it)."""
    st = _status(snap)
    title = "🐢 tommybot embeddings · the slow sync"
    if snap is None or not snap.get("ok"):
        body = f"{st.glyph} **embeddings DB unreachable** — no `embeddings.db` mounted/readable"
        return {"embeds": [{"title": title, "description": body, "color": st.color}]}

    header = f"{st.glyph} {snap['total_chunks']} chunks embedded"
    if st is tokens.DEGRADED:
        header += " · **no sync recently** — check the */15 trickle job"
    return {
        "embeds": [{"title": title, "description": f"{header}\n{_graph(snap, history)}", "color": st.color}]
    }


def _graph(snap: dict, history: list[int]) -> str:
    """The monospace graph block: braille growth chart + per-vault bars + last-sync + model."""
    total = max(1, snap["total_chunks"])
    lines = ["```text"]
    if len(history) >= 2:
        delta = history[-1] - history[0]
        lines.append(graph.braille(history, width=26, height=4))
        lines.append(f"chunks {snap['total_chunks']:,}  (+{delta:,} tracked)")
    else:
        lines.append(f"chunks {snap['total_chunks']:,}  (tracking growth from here)")
    lines.append("vaults")
    width = max(len(v) for v in VAULTS)
    for vault in VAULTS:
        v = snap["vaults"][vault]
        count = f"{v['chunks']:>6,}" if v["chunks"] else "     —"
        lines.append(f"  {vault:<{width}} {graph.bar(v['chunks'], total)} {count}")
    lines.append("```")

    last, last_vault = snap.get("last_sync"), snap.get("last_vault")
    if last and last.get("ts"):
        detail = f"embedded {last['embedded']}"
        if last.get("session"):
            detail += f" (changed {last['changed']} · rolled {last['rolled']})"
        lines.append(f"last sync <t:{int(last['ts'])}:R> · {last_vault} · {detail}")
    else:
        lines.append("last sync — never")

    model = snap.get("model") or "unknown"
    wal = snap.get("wal_bytes", 0)
    wal_note = f" · wal {wal // 1024} KB pending checkpoint" if wal > 4096 else ""
    lines.append(f"model {model}{wal_note}")
    return "\n".join(lines)


# --- demo: a scripted sequence so `--dry --demo` shows the sync progressing -----------------
_NOW = int(time.time())


def _snap(chunks: dict[str, int], embedded, changed, rolled, model, wal, sync_min_ago, last_vault):
    vaults = {
        v: {
            "files": chunks.get(v, 0),
            "chunks": chunks.get(v, 0),
            "last": {
                "ts": _NOW - sync_min_ago * 60,
                "embedded": embedded,
                "changed": changed,
                "rolled": rolled,
                "deleted": 0,
                "session": True,
            }
            if v == last_vault
            else None,
        }
        for v in VAULTS
    }
    return {
        "ok": True,
        "model": model,
        "vaults": vaults,
        "total_chunks": sum(chunks.values()),
        "wal_bytes": wal,
        "last_sync": vaults[last_vault]["last"],
        "last_vault": last_vault,
    }


_MODEL = "nomic-ai/nomic-embed-text-v1.5"
DEMO_SEQUENCE: list[dict | None] = [
    _snap({"camping": 96}, 48, 48, 0, _MODEL, 16384, 2, "camping"),
    _snap({"camping": 96}, 48, 48, 0, _MODEL, 16384, 2, "camping"),  # unchanged → skip
    _snap({"camping": 288, "dev": 48}, 48, 48, 0, _MODEL, 8192, 1, "dev"),
    _snap({"camping": 432, "dev": 240, "gear": 48}, 48, 12, 36, _MODEL, 0, 0, "gear"),
    None,  # DB unreachable
    _snap(
        {"camping": 432, "dev": 432, "gear": 288, "home": 96, "travel": 48},
        48, 40, 8, _MODEL, 12288, 4, "home",
    ),
]
DEMO_CAPTION = ["seeding camping", "no change", "camping → dev", "gear starts", "unreachable", "recovered"]
DEMO_HISTORY: list[list[int]] = []


def _demo_history(i: int) -> list[int]:
    """Synthetic growth history per demo tick (baked, since demo ticks don't really elapse time)."""
    bases = [
        [0, 20, 48, 72, 96],
        [0, 20, 48, 72, 96],
        [96, 150, 220, 280, 384],
        [384, 500, 620, 700, 720],
        [720],
        [720, 760, 900, 1050, 1296],
    ]
    return bases[i]


# --- the job (shared by the standalone daemon and live_service's inner loop) ----------------
def make_job(
    url: str | None,
    *,
    dry: bool = False,
    state: str,
    interval: float = 300,
    db_dir: str = "/mnt/tommybot-cache",
) -> Job:
    """One embeddings-sync tick as a live.Job — read the DB, regraph, reconcile."""
    dash = Dashboard(Poster(url, dry=dry), state_path=state, key="embed", source="embed-dashboard")
    db_path = Path(db_dir) / "embeddings.db"

    def tick() -> str:
        snap = read_db(db_path)
        history = record(state, snap["total_chunks"]) if snap else load_history(state)
        return dash.tick(build_panel(snap, history))

    return Job("embed", interval, tick)


# --- loop ----------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="the #ops tommybot embeddings sync graph (discokit)")
    ap.add_argument("--dry", action="store_true", help="print create/edit calls, post nothing")
    ap.add_argument("--demo", action="store_true", help="replay a scripted sync sequence")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between demo ticks")
    ap.add_argument("--interval", type=int, default=300, help="poll seconds (live mode)")
    ap.add_argument("--iterations", type=int, default=0, help="0 = forever (live mode)")
    ap.add_argument("--state", default=os.environ.get("EMBED_DASH_STATE", "/tmp/embed-dashboard.json"))
    ap.add_argument(
        "--db-dir",
        default=os.environ.get("TOMMYBOT_CACHE_DIR", "/mnt/tommybot-cache"),
        help="directory containing embeddings.db (read-only mount in the container)",
    )
    args = ap.parse_args()

    url = config.webhook("OPS")  # dedicated DISCORD_WEBHOOK_OPS, else the general webhook → #ops
    if not args.dry and not url:
        print("[embed_dashboard] no DISCORD_WEBHOOK_OPS / DISCORD_WEBHOOK_URL found", file=sys.stderr)
        sys.exit(1)

    run = "DEMO" if args.demo else "live"
    print(f"[*] embed sync — {run}{' · DRY' if args.dry else ''} · state={args.state}")

    if args.demo:
        dash = Dashboard(
            Poster(url, dry=args.dry), state_path=args.state, key="embed", source="embed-dashboard"
        )
        for i, snap in enumerate(DEMO_SEQUENCE):
            print(f"\ntick {i}  ({DEMO_CAPTION[i]})")
            print(f"  └─ → {dash.tick(build_panel(snap, _demo_history(i)))}")
            if args.delay and i < len(DEMO_SEQUENCE) - 1:
                time.sleep(args.delay)
        print("\n[done] one message, edited in place — no reposts.")
        return

    # same tick the live_service inner loop runs, just on a plain while/sleep here
    job = make_job(url, dry=args.dry, state=args.state, interval=args.interval, db_dir=args.db_dir)
    tick = 0
    while args.iterations == 0 or tick < args.iterations:
        print(f"[tick {tick}] {job.tick()}")
        tick += 1
        if args.iterations == 0 or tick < args.iterations:
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
