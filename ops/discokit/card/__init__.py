"""discokit.card — HTML→PNG chart cards for Discord embeds (Phase 3).

Discord embeds can't draw charts; a *card* is a designed PNG rendered from an
HTML panel styled by the same tokens.css the rest of the kit generates — so a
card's alert strip and an embed's side-stripe can never drift.

Layers:
    chrome   the card shell: dark panel, header + status pill, hero, footer —
             all colours from tokens.css vars, ECharts vendored for the charts
    render   Playwright (chromium) screenshots the panel element → PNG bytes,
             transparent page so the rounded corners blend into any Discord theme

The heavy dependency (a chromium) is deliberately isolated here: nothing else
in discokit imports card, so the webhook bots stay slim. Consumers compose a
card body, wrap it with chrome.page(), render it, and post it via
Poster.post_image().
"""

from . import chrome, render  # noqa: F401

__all__ = ["chrome", "render"]
