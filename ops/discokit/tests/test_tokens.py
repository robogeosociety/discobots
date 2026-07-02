# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest"]
# ///
"""Unit tests for discokit.tokens + build_tokens — the generated palette.

Run (from ops/, so `discokit` resolves as a package):
    cd ops && uv run --with pytest python -m pytest discokit/tests/test_tokens.py
"""

from __future__ import annotations

import json
from pathlib import Path

from discokit import build_tokens, tokens

KIT = Path(build_tokens.__file__).resolve().parent


def test_committed_outputs_match_tokens_json():
    """tokens.py and tokens.css are generated — fail if they drifted from source."""
    source = json.loads((KIT / "tokens.json").read_text())
    assert (KIT / "tokens.py").read_text() == build_tokens.emit_py(source)
    assert (KIT / "tokens.css").read_text() == build_tokens.emit_css(source)


def test_six_statuses_with_the_primer_dark_palette():
    assert len(tokens.ALL) == 6
    assert tokens.BY_KEY["operational"].color == 0x3FB950
    assert tokens.BY_KEY["critical"].color == 0xF85149
    assert all(s.glyph and s.dot and s.label for s in tokens.ALL)
    assert tokens.OPERATIONAL.dot == "🟢" and tokens.DEGRADED.dot == "🟡"


def test_accents_exist_for_the_non_status_hues():
    assert tokens.BLURPLE == 0x5865F2
    assert tokens.ORANGE == 0xDB6D28
    assert tokens.PURPLE == 0x8957E5


def test_up_down_maps_to_operational_and_critical():
    assert tokens.up_down(True) is tokens.OPERATIONAL
    assert tokens.up_down(False) is tokens.CRITICAL


def test_css_carries_the_same_colours_as_python():
    css = (KIT / "tokens.css").read_text()
    for s in tokens.ALL:
        assert f"--status-{s.key}: #{s.color:06x};" in css
    assert f"--accent-blurple: #{tokens.BLURPLE:06x};" in css
