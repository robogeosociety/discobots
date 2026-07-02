"""discokit.graph — btop-style text graphs for Discord surfaces (Phase 3).

Discord-native visualization: emoji, ASCII/Unicode, and markdown — rendered as
plain strings, live-updated by editing the message in place (dashboard.py).
No images, no browser, no chart library; a tick is a few string ops.

Two glyph families, two contexts — never mix them:
  INSIDE ``` code blocks   monospace-safe Unicode only: braille area charts
                           (⣀⣤⣶⣿ — 2×4 dots per cell, btop's trick), block
                           sparklines (▁▂▃▄▅▆▇█), proportional bars (█░).
                           Emojis are variable-width there and wreck alignment.
  OUTSIDE code blocks      the tokens' emoji dots (🟢🟡🔴…) for chip rows and
                           inline status, plus markdown bold and <t:…:R> stamps.

Sizing rule of thumb: a phone-width Discord code block holds ~30 monospace
chars before it wraps — default widths here stay under that.
"""

from __future__ import annotations

_SPARK = "▁▂▃▄▅▆▇█"

# Braille cell dot bits, top row → bottom row, (left, right) columns.
_BRAILLE_LEFT = (0x01, 0x02, 0x04, 0x40)
_BRAILLE_RIGHT = (0x08, 0x10, 0x20, 0x80)


def resample(values: list[float], n: int) -> list[float]:
    """Fit a series to exactly n points (linear interpolation, endpoints kept)."""
    if n <= 0 or not values:
        return []
    if len(values) == 1:
        return [float(values[0])] * n
    if n == 1:
        return [float(values[-1])]
    step = (len(values) - 1) / (n - 1)
    out = []
    for i in range(n):
        x = i * step
        j = int(x)
        frac = x - j
        right = values[min(j + 1, len(values) - 1)]
        out.append(values[j] * (1 - frac) + right * frac)
    return out


def spark(values: list[float], *, width: int | None = None) -> str:
    """One-line block sparkline. Flat (or single-value) series render full-height."""
    if not values:
        return "—"
    vals = resample([float(v) for v in values], width) if width else [float(v) for v in values]
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return _SPARK[-1] * len(vals)
    top = len(_SPARK) - 1
    return "".join(_SPARK[min(top, int((v - lo) / (hi - lo) * top + 0.5))] for v in vals)


def bar(value: float, total: float, *, width: int = 11) -> str:
    """A proportional block bar of fixed width (empty track when total is 0)."""
    if total <= 0:
        return "░" * width
    filled = max(0, min(width, round(value / total * width)))
    return "█" * filled + "░" * (width - filled)


def braille(values: list[float], *, width: int = 28, height: int = 4) -> str:
    """A btop-style area chart: ``height`` lines of braille, 2×4 dots per cell.

    The area fills from the baseline (series minimum) up, so a slow climb reads
    as a rising shoreline. Flat series draw a half-height plateau. Monospace-
    safe — for ``` code blocks only.
    """
    if not values:
        return ""
    cols, dots_h = width * 2, height * 4
    vals = resample([float(v) for v in values], cols)
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        levels = [max(1, dots_h // 2)] * cols
    else:
        levels = [1 + round((v - lo) / (hi - lo) * (dots_h - 1)) for v in vals]

    lines = []
    for row in range(height):
        chars = []
        for col in range(width):
            bits = 0
            for sub in range(4):
                dot_height = dots_h - (row * 4 + sub)  # 1 = baseline, dots_h = top
                if levels[col * 2] >= dot_height:
                    bits |= _BRAILLE_LEFT[sub]
                if levels[col * 2 + 1] >= dot_height:
                    bits |= _BRAILLE_RIGHT[sub]
            chars.append(chr(0x2800 + bits))
        lines.append("".join(chars))
    return "\n".join(lines)


def chips(items: list[tuple[str, str]], *, per_line: int = 4) -> str:
    """Emoji-dot chip rows: [(name, dot), …] → "🔴 api  🟢 web" lines.

    Dots are the tokens' emoji circles (Status.dot) — chips live OUTSIDE code
    blocks. Callers order the items (the fleet convention: down first).
    """
    parts = [f"{dot} {name}" for name, dot in items]
    return "\n".join("  ".join(parts[i : i + per_line]) for i in range(0, len(parts), per_line))
