"""discokit — shared design-language kit for the discobots fleet (Phase-2 spike).

This is a deliberately small slice: just enough to prove the in-place *dynamic
dashboard* (post once, PATCH-edit thereafter) that the #ops readout wants. The
full kit (embed builders, card renderer, notify, live/gateway) layers on top of
these same primitives — see docs/DESIGN_LANGUAGE.md (proposed).

Layers present in this spike:
    tokens     the one status palette (colour + glyph + label)
    config     resolve a named webhook (env → grafana/.env)
    poster     execute (?wait=true) + PATCH-edit, with 429 back-off
    dashboard  Dashboard.tick(): upsert once, diff, edit-in-place, <t:R> stamp
    guard      the private-guild allowlist any Discord-*reading* code must honor
"""

from . import config, dashboard, guard, poster, tokens  # noqa: F401

__all__ = ["tokens", "config", "poster", "dashboard", "guard"]
