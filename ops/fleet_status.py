#!/usr/bin/env python3
"""fleet_status — one source, two surfaces: the #ops fleet-status board.

`fleet.toml` is the single source of truth (bots / collectors / data sources /
graph kit). This renders it into two places that must never drift:

  • a pinned, edit-in-place **#ops Discord panel** — a directory of the fleet
    with a couple of sample text-native graphs and a link to the full page;
  • **docs/fleet-status.md** — the dev-wiki page (published via the `/wikime`
    skill), the fuller reference with per-item detail tables.

Both derive from `fleet.toml`, so editing the inventory once keeps them in
lock-step (a test asserts the committed page matches this renderer's output).

    python3 ops/fleet_status.py --markdown docs/fleet-status.md   # regen the page
    python3 ops/fleet_status.py --discord --dry                   # preview the panel
    python3 ops/fleet_status.py --discord                         # post/edit it (needs the #ops webhook)
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

_OPS = Path(__file__).resolve().parent
sys.path.insert(
    0, str(_OPS)
)  # discokit sits next to this file (and flat in /app in the container)

from discokit import config, graph, tokens  # noqa: E402
from discokit.dashboard import Dashboard  # noqa: E402
from discokit.poster import Poster  # noqa: E402

FLEET_TOML = _OPS / "fleet.toml"
REPO_ROOT = _OPS.parent
DEFAULT_MD = REPO_ROOT / "docs" / "fleet-status.md"

# Illustrative sample series — the fleet's graph vocabulary, not live telemetry
# (the real thing edits itself in #ops / #ops-watcher / #transit). Deterministic
# so the rendered page is stable across runs.
_SAMPLE_ACTIVITY = [0, 0, 1, 3, 2, 0, 0, 0, 4, 5, 2, 0, 0, 1, 0, 0, 2, 3, 1, 0, 0, 1, 2, 0]  # fmt: skip
_SAMPLE_MEMORY = [52, 55, 60, 66, 70, 66, 58, 50, 44, 40, 38, 41, 46, 50, 55, 60, 63, 66]  # fmt: skip


def load(path: str | Path = FLEET_TOML) -> dict:
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _dot(status: str) -> str:
    return tokens.BY_KEY.get(status, tokens.UNKNOWN).dot


def _sample_block() -> str:
    """A code-fenced pair of sample graphs (braille area + block spark)."""
    area = graph.braille(_SAMPLE_ACTIVITY, width=20, height=2)
    spark = graph.spark(_SAMPLE_MEMORY, width=20)
    return (
        "```\nactivity · jobs fired/min\n"
        + area
        + f"\nmemory · mini free %  {spark}\n```"
    )


# ── Discord panel (compact directory) ───────────────────────────────────────
def render_discord(inv: dict) -> dict:
    meta = inv.get("meta", {})
    bots = inv.get("bots", [])
    collectors = inv.get("collectors", [])
    sources = inv.get("data_sources", [])
    graphs = inv.get("graphs", [])

    desc = "\n".join(
        [
            f"_{meta.get('tagline', '')}_",
            "",
            _sample_block(),
            "",
            f"📖 full board: {meta.get('wiki_url', '')}",
        ]
    )

    def bots_val() -> str:
        return "\n".join(
            f"{_dot(b.get('status', 'unknown'))} `{b['name']}` · {b['channel']} · {b['cadence']}"
            for b in bots
        )

    def collectors_val() -> str:
        return "\n".join(
            f"{_dot(c.get('status', 'unknown'))} `{c['name']}` → {c['into']}"
            for c in collectors
        )

    def sources_val() -> str:
        return "\n".join(
            f"{_dot(s.get('status', 'unknown'))} `{s['name']}` — {s['serves']}"
            for s in sources
        )

    def graphs_val() -> str:
        return "\n".join(f"`{g['name']}` — {g['kinds']}" for g in graphs)

    embed = {
        "title": f"🛰️ {meta.get('title', 'fleet status')}",
        "color": tokens.BLURPLE,
        "description": desc,
        "fields": [
            {
                "name": f"🤖 ops bots ({len(bots)})",
                "value": bots_val(),
                "inline": False,
            },
            {
                "name": f"🌾 collectors ({len(collectors)})",
                "value": collectors_val(),
                "inline": False,
            },
            {
                "name": f"🗄️ data sources ({len(sources)})",
                "value": sources_val(),
                "inline": False,
            },
            {"name": "📈 graph kit", "value": graphs_val(), "inline": False},
        ],
        "footer": {"text": meta.get("routing_note", "")},
    }
    return {"embeds": [embed]}


# ── dev-wiki page (full reference) ──────────────────────────────────────────
def _table(headers: list[str], rows: list[list[str]]) -> str:
    line = "| " + " | ".join(headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join([line, sep, body])


def render_markdown(inv: dict) -> str:
    meta = inv.get("meta", {})
    bots = inv.get("bots", [])
    collectors = inv.get("collectors", [])
    sources = inv.get("data_sources", [])
    graphs = inv.get("graphs", [])

    def em(status: str) -> str:
        return tokens.BY_KEY.get(status, tokens.UNKNOWN).glyph

    out: list[str] = []
    out.append(f"# 🛰️ {meta.get('title', 'fleet status')}")
    out.append("")
    out.append(f"_{meta.get('tagline', '')}_")
    out.append("")
    out.append(
        "> This page is generated from `ops/fleet.toml` and mirrors the pinned **#discobots** "
        "Discord panel (refreshed by CI/CD on every deploy). The live telemetry is elsewhere in "
        "Discord — **#ops** (loop + supervisor), **#ops-watcher** (dev status), **#transit** "
        "(lines). This board is the directory."
    )
    out.append("")

    out.append("## 🤖 Discord ops bots")
    out.append("")
    out.append(
        _table(
            ["", "bot", "channel", "cadence", "what", "repo"],
            [
                [
                    em(b.get("status", "unknown")),
                    f"`{b['name']}`",
                    b["channel"],
                    b["cadence"],
                    b["what"],
                    b["repo"],
                ]
                for b in bots
            ],
        )
    )
    out.append("")

    out.append("## 🌾 Collectors")
    out.append("")
    out.append(
        _table(
            ["", "collector", "source", "feeds"],
            [
                [
                    em(c.get("status", "unknown")),
                    f"`{c['name']}`",
                    c["source"],
                    c["into"],
                ]
                for c in collectors
            ],
        )
    )
    out.append("")

    out.append("## 🗄️ Data sources")
    out.append("")
    out.append(
        _table(
            ["", "source", "kind", "serves"],
            [
                [
                    em(s.get("status", "unknown")),
                    f"`{s['name']}`",
                    s["kind"],
                    s["serves"],
                ]
                for s in sources
            ],
        )
    )
    out.append("")

    out.append("## 📈 Graph kit")
    out.append("")
    out.append(
        _table(
            ["kit", "kinds", "repo"],
            [[f"`{g['name']}`", g["kinds"], g["repo"]] for g in graphs],
        )
    )
    out.append("")
    out.append(
        "Sample of the text-native vocabulary (the real panels edit themselves in place):"
    )
    out.append("")
    out.append(_sample_block())
    out.append("")

    out.append("## 🔁 Keeping it in sync")
    out.append("")
    out.append(
        f"`ops/fleet.toml` is the single source of truth. {meta.get('routing_note', '')}"
    )
    out.append("")
    out.append("1. Edit `ops/fleet.toml`.")
    out.append(
        "2. `python3 ops/fleet_status.py --markdown docs/fleet-status.md` — regenerate this page (a test asserts it matches)."
    )
    out.append(
        "3. Commit + merge — CI/CD ships it: the mini's autodeploy poller repaints the pinned "
        "**#discobots** panel from the same file (or run `just fleet-status` to repaint it now)."
    )
    out.append("4. `/wikime` publishes this page to the dev wiki.")
    out.append("")
    out.append(
        "_Generated from `ops/fleet.toml` by `ops/fleet_status.py` — do not hand-edit._"
    )
    return "\n".join(out) + "\n"


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="the #discobots fleet-status board (one source → panel + wiki page)"
    )
    ap.add_argument(
        "--discord", action="store_true", help="create/edit the pinned #discobots panel"
    )
    ap.add_argument(
        "--markdown",
        nargs="?",
        const=str(DEFAULT_MD),
        help="write the wiki page (default docs/fleet-status.md)",
    )
    ap.add_argument("--all", action="store_true", help="both --discord and --markdown")
    ap.add_argument(
        "--dry", action="store_true", help="preview the panel, post nothing"
    )
    ap.add_argument("--toml", default=str(FLEET_TOML), help="inventory path")
    ap.add_argument(
        "--state",
        default=os.environ.get("FLEET_STATUS_STATE", "/tmp/fleet-status.json"),
    )
    args = ap.parse_args()

    inv = load(args.toml)

    if args.markdown or args.all:
        path = Path(args.markdown) if isinstance(args.markdown, str) else DEFAULT_MD
        path.write_text(render_markdown(inv))
        print(f"[fleet_status] wrote {path}")

    if args.discord or args.all:
        # #discobots is the board's home; the shell wrappers (just fleet-status /
        # autodeploy.sh) resolve DISCOBOTS→OPS→URL and pass DISCORD_WEBHOOK_DISCOBOTS.
        url = config.webhook("DISCOBOTS")
        if not args.dry and not url:
            print(
                "[fleet_status] no DISCORD_WEBHOOK_DISCOBOTS / DISCORD_WEBHOOK_URL found",
                file=sys.stderr,
            )
            sys.exit(1)
        dash = Dashboard(
            Poster(url, dry=args.dry),
            state_path=args.state,
            key="fleet",
            source="fleet-status",
        )
        print(f"[fleet_status] panel → {dash.tick(render_discord(inv))}")

    if not (args.markdown or args.discord or args.all):
        ap.error("nothing to do — pass --markdown, --discord, or --all")


if __name__ == "__main__":
    main()
