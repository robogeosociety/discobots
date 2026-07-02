"""discokit.card.render — HTML panel → PNG bytes, via Playwright chromium.

The page background is transparent and only the ``#card`` element is
screenshotted, so the PNG carries the panel's rounded corners over whatever
Discord theme it lands on. ``device_scale_factor=2`` renders retina-crisp for
both the desktop embed column (~500 px) and full-width phone embeds.

Playwright is imported lazily — the webhook bots never touch this module, and
the renderer runs wherever a chromium lives (the Air's browser-automation venv
during design iteration; its production home is decided at rollout).
"""

from __future__ import annotations


def png(html: str, *, scale: int = 2, timeout_ms: int = 15000) -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": 1400, "height": 1200},
                device_scale_factor=scale,
            )
            page.set_content(html, wait_until="load", timeout=timeout_ms)
            # charts_js sets __cardReady after the ECharts init has run
            page.wait_for_function("window.__cardReady === true", timeout=timeout_ms)
            page.wait_for_timeout(150)  # let the chart canvas paint its first frame
            return page.locator("#card").screenshot(omit_background=True)
        finally:
            browser.close()
