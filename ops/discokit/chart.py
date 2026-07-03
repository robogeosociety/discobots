"""discokit.chart — rendered PNG charts, for the one thing text can't do.

`graph.py`'s braille/sparkline registers are the default, and PR #19
(Playwright → PNG cards) was shelved after review: "the fleet prefers
Discord-native text ... over rendered PNGs." This module doesn't reopen that
verdict — it exists for the narrow case text genuinely can't cover: a real
numeric axis with more than one labeled, legended series. Reach for
`graph.py` first; reach for this only when a legend or an axis label is the
actual requirement, not a decoration.

No Chromium: matplotlib's Agg backend rasterizes straight to PNG bytes in
this process. It's still blocking CPU work — call it via `asyncio.to_thread`
like any other tick, same as an InfluxDB query. Lazily imported (like
poster.py's httpx) so bots that never chart stay slim, and matplotlib is
deliberately NOT in the shared base image — a bot opts in by adding it to
its own Dockerfile, the same isolation PR #19 used for its Chromium
dependency.
"""

from __future__ import annotations

import io

# Discord's dark theme surface colours (not a token — this is chart chrome,
# not a status), so a chart's background/gridlines match the embed it sits in.
_BG = "#2B2D31"
_GRID = "#4E5058"
_TEXT = "#DBDEE1"
_MUTED = "#B5BAC1"


def timeseries(
    series: dict[str, list[float]],
    *,
    timestamps: list[float] | None = None,
    title: str = "",
    ylabel: str = "",
    width_px: int = 800,
    height_px: int = 400,
) -> bytes:
    """Render a multi-series line chart to PNG bytes, dark-themed for an embed.

    ``series`` is ``{label: values}`` — one line + legend entry per key,
    colored from the tokens' accent/status hues (cycling if there are more
    series than hues). ``timestamps`` (epoch seconds) labels the x-axis;
    omit it for a plain sample-index axis. An empty (or all-empty) ``series``
    still returns a valid placeholder PNG rather than raising, matching
    graph.py's graceful-empty-input convention.
    """
    import matplotlib

    matplotlib.use("Agg")
    import datetime

    import matplotlib.pyplot as plt

    from . import tokens

    fig, ax = plt.subplots(figsize=(width_px / 100, height_px / 100), dpi=100)
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    has_data = any(values for values in series.values())
    if not has_data:
        ax.text(
            0.5,
            0.5,
            "no data",
            ha="center",
            va="center",
            color=_MUTED,
            fontsize=12,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        hues = [tokens.BLURPLE, tokens.ORANGE, tokens.PURPLE] + [
            s.color for s in tokens.ALL
        ]
        x = (
            [datetime.datetime.fromtimestamp(t) for t in timestamps]
            if timestamps
            else None
        )
        for i, (label, values) in enumerate(series.items()):
            color = f"#{hues[i % len(hues)]:06X}"
            xs = x if x is not None else list(range(len(values)))
            ax.plot(xs, values, label=label, color=color, linewidth=1.75)

        ax.tick_params(colors=_MUTED, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(_GRID)
        ax.grid(True, color=_GRID, linewidth=0.5, alpha=0.5)
        if len(series) > 1:
            legend = ax.legend(loc="upper left", fontsize=9, framealpha=0.0)
            for text in legend.get_texts():
                text.set_color(_TEXT)
        if x is not None:
            fig.autofmt_xdate()

    if title:
        ax.set_title(title, color=_TEXT, fontsize=13, loc="left")
    if ylabel:
        ax.set_ylabel(ylabel, color=_MUTED, fontsize=10)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


if __name__ == "__main__":
    # Preview the look with no Discord/bot wiring, matching the fleet's
    # `--dry --demo` convention. Uses a relative import (sibling to every
    # other discokit module), so run it as a module from ops/:
    #   cd ops && python3 -m discokit.chart discokit/chart-demo.png
    import sys

    out = sys.argv[1] if len(sys.argv) > 1 else "chart-demo.png"
    minutes = list(range(30))
    demo = timeseries(
        {
            "cpu %": [40 + 15 * ((i % 7) - 3) / 3 + i * 0.4 for i in minutes],
            "mem %": [55 + i * 0.9 for i in minutes],
        },
        title="mac-system — last 30 min",
        ylabel="percent",
    )
    with open(out, "wb") as f:
        f.write(demo)
    print(f"wrote {out} ({len(demo)} bytes)")
