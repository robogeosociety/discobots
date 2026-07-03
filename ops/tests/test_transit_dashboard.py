"""Hermetic tests for transit_dashboard.build_panel (no network, no httpx/gtfs).

Pins the collapse behaviour: one embed whose colour + chip row + disruption list
reflect the worst active effect per watched line, down-first, and that an
unreachable feed degrades to last-known state instead of blanking.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import transit_dashboard as tdash  # noqa: E402
from discokit import tokens  # noqa: E402


def _embed(payload):
    return payload["embeds"][0]


def test_all_clear_is_green_and_lists_no_disruptions():
    e = _embed(tdash.build_panel([], last_good=[]))
    assert e["color"] == tokens.OPERATIONAL.color
    assert f"{len(tdash.LINES)}/{len(tdash.LINES)} lines clear" in e["description"]
    # No "**Line** — header" disruption rows when everything is clear.
    assert "—" not in e["description"].split("clear")[-1]


def test_a_major_effect_turns_the_line_and_panel_red():
    alerts = [{"routes": ["2 Line"], "effect": "NO_SERVICE", "header": "No service, track work"}]
    e = _embed(tdash.build_panel(alerts, last_good=[]))
    assert e["color"] == tokens.CRITICAL.color
    assert f"{tokens.CRITICAL.dot} **2 Line** — No service, track work" in e["description"]
    # Untouched lines stay clear → the panel reports 5/6.
    assert f"{len(tdash.LINES) - 1}/{len(tdash.LINES)} lines clear" in e["description"]


def test_worst_effect_wins_and_disruptions_lead_down_first():
    alerts = [
        {"routes": ["Route 7"], "effect": "REDUCED_SERVICE", "header": "minor reroute"},
        {"routes": ["1 Line"], "effect": "NO_SERVICE", "header": "down"},
    ]
    desc = _embed(tdash.build_panel(alerts, last_good=[]))["description"]
    chip_row = desc.splitlines()[1]  # first line after the header summary
    # Down-first: the red line's chip precedes the orange one in the row.
    assert chip_row.index("1 Line") < chip_row.index("Route 7")


def test_unreachable_feed_shows_last_known_not_blank():
    last = [{"routes": ["2 Line"], "effect": "SIGNIFICANT_DELAYS", "header": "delays"}]
    e = _embed(tdash.build_panel(None, last_good=last))
    assert "unreachable" in e["description"]
    assert "2 Line" in e["description"]  # last-known disruption still rendered


def test_unknown_line_in_alert_is_ignored():
    e = _embed(tdash.build_panel([{"routes": ["Route 999"], "effect": "NO_SERVICE", "header": "x"}], last_good=[]))
    assert e["color"] == tokens.OPERATIONAL.color  # nothing watched was hit
