"""discokit — shared design-language kit for the discobots fleet.

One token source + reusable layers for the fleet's Discord surfaces. The card
renderer (Phase 3) and live/gateway (Phase 4) layer on top of these same
primitives.

Layers:
    tokens     the one status palette (colour + glyph + label) — GENERATED from
               tokens.json by build_tokens.py, alongside tokens.css for cards
    config     resolve a named webhook (env → grafana/.env) + dotenv reader
    poster     batched notify POST, execute (?wait=true), PATCH-edit, 429 back-off
    botmsg     bot-token edit-in-place of ONE message (poster's sibling for the
               edit-a-single-message dashboards; webhooks can't edit arbitrary msgs)
    notify     StateFile (durable JSON) + ChangeFeed (announce each id once)
    dashboard  Dashboard.tick(): upsert once, diff, edit-in-place, <t:R> stamp
    daemon     serve(): the watchdog tick loop — a per-tick SIGALRM deadline so a
               hung syscall (e.g. DNS) can't wedge a dashboard the way it did once
    treemap    squarified colored-square emoji treemap (the memory dashboards)
    live       the asyncio inner loop — many recurring Jobs, one process
    graph      btop-style text graphs: braille charts, sparklines, bars, chips
    art        MI1-discipline ASCII scenes: density ramps, Bayer dither, bands
    bus        the fleet message bus (Valkey/Redis) — degradable pub/sub + streams
    guard      the private-guild allowlist any Discord-*reading* code must honor
"""

from . import (  # noqa: F401
    art,
    botmsg,
    bus,
    config,
    daemon,
    dashboard,
    graph,
    guard,
    live,
    notify,
    poster,
    tokens,
    treemap,
)

__all__ = [
    "tokens", "config", "notify", "poster", "botmsg", "dashboard", "daemon",
    "treemap", "live", "graph", "art", "bus", "guard",
]
