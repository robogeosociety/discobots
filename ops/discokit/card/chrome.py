"""discokit.card.chrome — the card shell: one designed dark panel.

The panel is tuned for where it lives: inside a Discord embed, on the dark
theme's #2b2d31 surface (and gracefully on light, thanks to the transparent
page + rounded corners). Colours come from tokens.css — the SAME generated
file the Phase-3 plan promised the cards — plus a thin layer of card-only
chrome (surface, hairlines, type scale).

Composition model: a consumer builds a body (hero numbers, a chart div, bars,
a footer) out of the helpers here, then wraps it with ``page()`` and hands the
HTML to ``render.png()``.
"""

from __future__ import annotations

import json
from pathlib import Path

_KIT = Path(__file__).resolve().parent.parent
_VENDOR = Path(__file__).resolve().parent / "vendor"

# Card-only chrome on top of the token palette. Surfaces sit just below
# Discord's dark embed background (#2b2d31) so the panel reads as its own
# object; text greys are Primer dark's fg scale (matching the token greys).
CARD_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { background: transparent; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", "Noto Sans", sans-serif;
  -webkit-font-smoothing: antialiased;
  color: #e6edf3;
}
#card {
  width: var(--card-width, 1080px);
  background: linear-gradient(180deg, #1e2025 0%, #17181c 100%);
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 20px;
  padding: 36px 40px 30px;
  overflow: hidden;
}
.card-header { display: flex; align-items: center; gap: 16px; }
.card-glyph {
  width: 52px; height: 52px; border-radius: 14px;
  display: flex; align-items: center; justify-content: center;
  font-size: 26px;
  background: var(--glyph-bg, rgba(88, 166, 255, 0.12));
}
.card-titles { flex: 1; min-width: 0; }
.card-title { font-size: 21px; font-weight: 700; letter-spacing: -0.01em; }
.card-subtitle { font-size: 14px; color: #8b949e; margin-top: 3px; }
.card-pill {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; font-weight: 600; letter-spacing: 0.02em;
  padding: 7px 14px; border-radius: 999px;
  background: color-mix(in srgb, var(--pill-color) 14%, transparent);
  color: var(--pill-color);
  border: 1px solid color-mix(in srgb, var(--pill-color) 30%, transparent);
}
.card-pill::before {
  content: ""; width: 8px; height: 8px; border-radius: 50%;
  background: var(--pill-color);
}
.card-hero { display: flex; align-items: baseline; gap: 14px; margin: 26px 0 4px; }
.hero-number {
  font-size: 54px; font-weight: 800; letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.hero-label { font-size: 15px; color: #8b949e; }
.hero-delta { font-size: 15px; font-weight: 600; margin-left: auto; }
.card-footer {
  display: flex; align-items: center; gap: 10px;
  margin-top: 24px; padding-top: 18px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
  font-size: 13px; color: #8b949e;
}
.card-footer .sep { opacity: 0.45; }
.card-footer .mono {
  font-family: "SF Mono", ui-monospace, "JetBrains Mono", Menlo, monospace;
  font-size: 12px;
}
"""


def tokens_css() -> str:
    """The generated palette vars, verbatim (single source with the embeds)."""
    return (_KIT / "tokens.css").read_text()


def echarts_js() -> str:
    return (_VENDOR / "echarts.min.js").read_text()


def page(body: str, *, width: int = 1080, extra_css: str = "", charts_js: str = "") -> str:
    """Wrap a card body in the full HTML page Playwright renders.

    ``charts_js`` is the consumer's ECharts init code; the vendored library is
    inlined so rendering needs no network.
    """
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<style>{tokens_css()}
:root {{ --card-width: {width}px; }}
{CARD_CSS}
{extra_css}</style>
<script>{echarts_js()}</script>
</head><body>
<div id="card">{body}</div>
<script>
{charts_js}
window.__cardReady = true;
</script>
</body></html>"""


def header(glyph: str, title: str, subtitle: str, *, pill_text: str, pill_color: str,
           glyph_bg: str | None = None) -> str:
    """The card's top row: glyph tile · title/subtitle · status pill."""
    tile_bg = glyph_bg or f"color-mix(in srgb, {pill_color} 12%, transparent)"
    return f"""
<div class="card-header">
  <div class="card-glyph" style="--glyph-bg: {tile_bg}">{glyph}</div>
  <div class="card-titles">
    <div class="card-title">{title}</div>
    <div class="card-subtitle">{subtitle}</div>
  </div>
  <div class="card-pill" style="--pill-color: {pill_color}">{pill_text}</div>
</div>"""


def hero(number: str, label: str, *, delta: str = "", delta_color: str = "#3fb950") -> str:
    """The big number row."""
    delta_html = f'<div class="hero-delta" style="color:{delta_color}">{delta}</div>' if delta else ""
    return f"""
<div class="card-hero">
  <div class="hero-number">{number}</div>
  <div class="hero-label">{label}</div>
  {delta_html}
</div>"""


def footer(*parts: str) -> str:
    """The muted meta strip; parts joined with a dot separator."""
    joined = '<span class="sep">·</span>'.join(f"<span>{p}</span>" for p in parts if p)
    return f'<div class="card-footer">{joined}</div>'


def js_value(value: object) -> str:
    """Safely inline a Python value into the chart-init JS.

    json.dumps leaves "</" intact, which would close the inline <script> tag
    early if a value ever carried "</script>" — escape it.
    """
    return json.dumps(value).replace("</", "<\\/")
