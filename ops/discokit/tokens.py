"""discokit.tokens — the ONE status palette (spike; later generated from tokens.json).

Six semantic states, each a single (colour, glyph, label). Colours are the
Primer *dark-mode* hexes: they clear WCAG 4.5:1 on Discord's near-black default
and separate warn/crit on brightness (colourblind-safe), not the red/green axis.
The glyph carries the meaning by *shape* so it survives with no colour vision.

Later this module is emitted by lib/tokens/build.py from lib/tokens/tokens.json,
alongside a matching tokens.css for the HTML→PNG cards — one source, two targets,
so an embed's side-stripe and a card's alert strip can never drift.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Status:
    key: str
    color: int   # Discord embed colour (int form of the hex)
    glyph: str   # shape-distinct marker; meaning survives colourblindness
    label: str


OPERATIONAL = Status("operational", 0x3FB950, "✅", "operational")
INFO        = Status("info",        0x58A6FF, "ℹ️", "info")
DEGRADED    = Status("degraded",    0xD29922, "⚠️", "degraded")
CRITICAL    = Status("critical",    0xF85149, "🔴", "critical")
MAINTENANCE = Status("maintenance", 0xA371F7, "🛠️", "maintenance")
UNKNOWN     = Status("unknown",     0x8B949E, "⚪", "unknown")

ALL = (OPERATIONAL, INFO, DEGRADED, CRITICAL, MAINTENANCE, UNKNOWN)
BY_KEY = {s.key: s for s in ALL}


def up_down(is_up: bool) -> Status:
    """Per-item marker: operational when up, critical when down."""
    return OPERATIONAL if is_up else CRITICAL
