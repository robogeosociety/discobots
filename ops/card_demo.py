#!/usr/bin/env python3
"""card_demo — render the discokit Phase-3 cards from scripted data, to a PNG.

The card renderer's `--dry --demo`: reproduces the design-review cards with
synthetic data, no live sources needed. Needs a Playwright chromium at
runtime (design iteration used the Air's browser-automation venv; the card
layer's production host is decided at rollout — see discokit/card/__init__.py).

    ~/.claude/tools/browser/venv/bin/python ops/card_demo.py embed --out /tmp/card.png
    ~/.claude/tools/browser/venv/bin/python ops/card_demo.py embed --state critical --out /tmp/card.png
    ~/.claude/tools/browser/venv/bin/python ops/card_demo.py fleet --out /tmp/card.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from discokit import tokens  # noqa: E402
from discokit.card import chrome, render  # noqa: E402

VAULTS = ("camping", "dev", "gear", "home", "travel")
VAULT_COLORS = {
    "camping": f"#{tokens.OPERATIONAL.color:06x}",
    "dev": f"#{tokens.INFO.color:06x}",
    "gear": f"#{tokens.DEGRADED.color:06x}",
    "home": f"#{tokens.MAINTENANCE.color:06x}",
    "travel": f"#{tokens.BLURPLE:06x}",
}

EMBED_CSS = """
#growth { height: 200px; margin: 6px 0 10px; }
.vaults { display: flex; flex-direction: column; gap: 9px; margin-top: 8px; }
.vault-row { display: flex; align-items: center; gap: 14px; font-size: 14px; }
.vault-name { width: 84px; color: #8b949e; text-align: right; }
.vault-track { flex: 1; height: 10px; border-radius: 5px; background: rgba(255,255,255,0.06); overflow: hidden; }
.vault-fill { height: 100%; border-radius: 5px; }
.vault-count { width: 64px; font-variant-numeric: tabular-nums; color: #e6edf3; font-weight: 600; }
.vault-count .zero { color: #565d66; font-weight: 400; }
.void { text-align: center; padding: 64px 0 58px; }
.void-dot { width: 18px; height: 18px; border-radius: 50%; margin: 0 auto 18px;
            background: #f85149; box-shadow: 0 0 0 7px rgba(248, 81, 73, 0.12); }
.void-title { font-size: 22px; font-weight: 700; }
.void-sub { font-size: 14px; color: #8b949e; margin-top: 8px; }
.void .mono { font-family: "SF Mono", ui-monospace, Menlo, monospace; font-size: 13px; }
"""


def embed_card(state: str) -> str:
    """The embeddings-sync card: hero total, trickle growth chart, vault bars."""
    subtitle = "the slow sync · trickle ≤48 chunks / 15 min"
    if state == "critical":
        body = (
            chrome.header("🐢", "tommybot embeddings", subtitle,
                          pill_text="UNREACHABLE", pill_color=f"#{tokens.CRITICAL.color:06x}",
                          glyph_bg="rgba(88, 166, 255, 0.10)")
            + """
<div class="void">
  <div class="void-dot"></div>
  <div class="void-title">embeddings DB unreachable</div>
  <div class="void-sub">no <span class="mono">embeddings.db</span> mounted or readable — showing nothing rather than something stale</div>
</div>"""
            + chrome.footer("last known total 11,456", "last sync 19 h ago · camping")
        )
        return chrome.page(body, extra_css=EMBED_CSS)

    st = tokens.INFO if state == "healthy" else tokens.DEGRADED
    pill = "HEALTHY" if state == "healthy" else "STALE"
    ago = "4 min ago" if state == "healthy" else "19 h ago"
    total, chunks = 11_456, {"camping": 10_990, "home": 466}
    history, v = [], total - 48 * 26
    for i in range(27):
        history.append(v)
        v += 0 if i % 6 == 4 else 48 if i % 3 else 44
    history = [h for h in history if h <= total] + [total]
    accent = f"#{tokens.INFO.color:06x}"

    bars = ""
    for vault in VAULTS:
        n = chunks.get(vault, 0)
        pct = n / total * 100
        fill = (f'<div class="vault-fill" style="width:{max(1.2, pct):.1f}%; background:{VAULT_COLORS[vault]}"></div>'
                if n else "")
        count = f"{n:,}" if n else '<span class="zero">—</span>'
        bars += (f'<div class="vault-row"><div class="vault-name">{vault}</div>'
                 f'<div class="vault-track">{fill}</div><div class="vault-count">{count}</div></div>')

    body = (
        chrome.header("🐢", "tommybot embeddings", subtitle,
                      pill_text=pill, pill_color=f"#{st.color:06x}",
                      glyph_bg="rgba(88, 166, 255, 0.10)")
        + chrome.hero(f"{total:,}", "chunks embedded · last 8 h",
                      delta=f"+{history[-1] - history[0]:,} tracked",
                      delta_color=f"#{tokens.OPERATIONAL.color:06x}")
        + '<div id="growth"></div>'
        + f'<div class="vaults">{bars}</div>'
        + chrome.footer(f"last sync {ago} · camping", '<span class="mono">BAAI/bge-small-en-v1.5</span>')
    )
    charts_js = f"""
const hist = {chrome.js_value(history)};
const c = echarts.init(document.getElementById('growth'), null, {{renderer: 'canvas'}});
c.setOption({{
  animation: false,
  grid: {{ left: 46, right: 8, top: 12, bottom: 22 }},
  xAxis: {{ type: 'category', data: hist.map((_, i) => i),
            axisLine: {{ show: false }}, axisTick: {{ show: false }}, axisLabel: {{ show: false }} }},
  yAxis: {{ type: 'value', min: v => Math.floor(v.min / 100) * 100, splitNumber: 3,
            splitLine: {{ lineStyle: {{ color: 'rgba(255,255,255,0.05)' }} }},
            axisLabel: {{ color: '#8b949e', fontSize: 11, formatter: v => v.toLocaleString('en-US') }} }},
  series: [{{ type: 'line', data: hist, smooth: 0.4, symbol: 'none',
              lineStyle: {{ width: 3, color: '{accent}' }},
              areaStyle: {{ color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                {{ offset: 0, color: '{accent}59' }}, {{ offset: 1, color: '{accent}00' }} ]) }} }}],
}});
"""
    return chrome.page(body, extra_css=EMBED_CSS, charts_js=charts_js)


def fleet_card() -> str:
    """The dev-status fleet card: hero up-count + a down-first chip grid."""
    services = [("web", True), ("api", False), ("grafana", True), ("nomad", True),
                ("ticks", True), ("walksheds", False), ("travel-wiki", True), ("tt-api", True)]
    services.sort(key=lambda kv: (kv[1], kv[0]))
    up = sum(1 for _, ok in services if ok)
    down = len(services) - up
    st = tokens.OPERATIONAL if down == 0 else tokens.CRITICAL
    green, red = f"#{tokens.OPERATIONAL.color:06x}", f"#{tokens.CRITICAL.color:06x}"

    chips = "".join(
        f'<div class="chip {"up" if ok else "down"}"><span class="chip-dot"></span>{name}</div>'
        for name, ok in services
    )
    body = (
        chrome.header("🖥️", "dev status", "the mini's deployment fleet · dev-status :8077",
                      pill_text="ALL UP" if down == 0 else f"{down} DOWN",
                      pill_color=f"#{st.color:06x}", glyph_bg="rgba(139, 148, 158, 0.10)")
        + chrome.hero(f"{up}<span class='hero-dim'>/{len(services)}</span>", "services up",
                      delta=f"{down} down" if down else "fleet green",
                      delta_color=red if down else green)
        + f'<div class="chips">{chips}</div>'
        + chrome.footer("down services listed first", "source dev-status · mini")
    )
    extra_css = f"""
.hero-dim {{ color: #565d66; font-weight: 700; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
.chip {{ display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 500;
        padding: 8px 14px; border-radius: 10px;
        background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.05); color: #c9d1d9; }}
.chip-dot {{ width: 7px; height: 7px; border-radius: 50%; }}
.chip.up .chip-dot {{ background: {green}; }}
.chip.down {{ background: color-mix(in srgb, {red} 9%, transparent);
             border-color: color-mix(in srgb, {red} 28%, transparent); color: #f0b8b4; }}
.chip.down .chip-dot {{ background: {red}; box-shadow: 0 0 0 3px color-mix(in srgb, {red} 18%, transparent); }}
"""
    return chrome.page(body, extra_css=extra_css)


def main() -> None:
    ap = argparse.ArgumentParser(description="render a demo discokit card to PNG")
    ap.add_argument("card", choices=["embed", "fleet"])
    ap.add_argument("--state", choices=["healthy", "stale", "critical"], default="stale",
                    help="embed card only: which status state to render")
    ap.add_argument("--out", default="/tmp/discokit-card.png")
    args = ap.parse_args()

    t0 = time.time()
    html = embed_card(args.state) if args.card == "embed" else fleet_card()
    Path(args.out).write_bytes(render.png(html))
    print(f"wrote {args.out} in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
