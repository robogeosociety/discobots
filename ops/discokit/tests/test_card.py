# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.card.chrome — HTML assembly (no chromium needed).

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_card.py
"""

from __future__ import annotations

from discokit.card import chrome


def test_page_inlines_the_generated_token_palette():
    html = chrome.page("<p>x</p>")
    assert "--status-critical: #f85149;" in html  # tokens.css, verbatim
    assert "--accent-blurple: #5865f2;" in html
    assert 'id="card"' in html


def test_page_inlines_echarts_and_signals_readiness():
    html = chrome.page("<p>x</p>", charts_js="init();")
    assert "Apache" in html[:6000] or "echarts" in html  # vendored lib present
    assert "init();" in html
    assert "__cardReady = true" in html


def test_header_hero_footer_compose():
    body = (
        chrome.header("🐢", "title", "sub", pill_text="HEALTHY", pill_color="#58a6ff")
        + chrome.hero("1,234", "things", delta="+56", delta_color="#3fb950")
        + chrome.footer("a", "", "b")
    )
    assert "HEALTHY" in body and "--pill-color: #58a6ff" in body
    assert "1,234" in body and "+56" in body
    assert body.count('<span class="sep">') == 1  # empty footer parts dropped


def test_js_value_escapes_safely():
    assert chrome.js_value([1, 2]) == "[1, 2]"
    # a value carrying "</script>" must not be able to close the inline script tag
    assert "</script>" not in chrome.js_value("x</script>y")
    assert chrome.js_value("x</script>y") == '"x<\\/script>y"'
