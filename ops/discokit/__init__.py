"""discokit — shared design-language kit for the discobots fleet.

One token source + reusable layers for the fleet's Discord surfaces. The card
renderer (Phase 3) and live/gateway (Phase 4) layer on top of these same
primitives.

Layers:
    tokens     the one status palette (colour + glyph + label) — GENERATED from
               tokens.json by build_tokens.py, alongside tokens.css for cards
    config     resolve a named webhook (env → grafana/.env) + dotenv reader
    poster     batched notify POST, execute (?wait=true), PATCH-edit, 429 back-off
    notify     StateFile (durable JSON) + ChangeFeed (announce each id once)
    dashboard  Dashboard.tick(): upsert once, diff, edit-in-place, <t:R> stamp
    guard      the private-guild allowlist any Discord-*reading* code must honor
"""

from . import config, dashboard, guard, notify, poster, tokens  # noqa: F401

__all__ = ["tokens", "config", "notify", "poster", "dashboard", "guard"]
