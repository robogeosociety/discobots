"""discokit.treemap — squarified emoji treemap, shared by the memory dashboards.

The colored-square-emoji treemap that `minimem` (host memory) and `orbmem`
(per-container memory) both render — one squarify layout, one grid-fill, one
legend. Emoji (not ANSI) so Discord colors them on mobile too, not just desktop.

`squarify()` and `grid_and_legend()` are pure and hermetically tested; callers add
their own header line (the two dashboards phrase "used/total" differently) and
wrap the pieces as `f"{header}\n\n{body}\n\n{legend}"`, so the rendered output is
byte-identical to the standalone bots this replaced.
"""

from __future__ import annotations

# 7 distinct squares that render in color on every Discord client (desktop AND
# mobile) — the palette length also caps how many tiles a treemap shows.
EMOJI = ["🟧", "🟦", "🟩", "🟪", "🟥", "🟨", "🟫"]

W, H = 12, 8  # grid the treemap is rasterized into (12 wide × 8 tall)


def human_mb(mb: float) -> str:
    """MB → a compact human string (1024 MB → '1.0G', else '512M')."""
    if mb >= 1024:
        return f"{mb / 1024:.1f}G"
    return f"{mb:.0f}M"


def squarify(items, x, y, w, h):
    """Bruls/Huizing/van Wijk squarified treemap → [label, value, x, y, w, h] rects."""
    rects = []
    items = sorted([i for i in items if i[1] > 0], key=lambda z: -z[1])

    def worst(row, length, scale):
        s = sum(v for _, v in row) * scale
        if s <= 0:
            return float("inf")
        mx = max(v for _, v in row) * scale
        mn = min(v for _, v in row) * scale
        return max(length * length * mx / (s * s), s * s / (length * length * mn))

    def place(row, x, y, w, h, horiz, scale):
        rs = sum(v for _, v in row) * scale
        if horiz:
            rw = rs / h if h > 0 else 0
            oy = y
            for lab, v in row:
                rh = (v * scale) / rw if rw > 0 else 0
                rects.append([lab, v, x, oy, rw, rh])
                oy += rh
        else:
            rh = rs / w if w > 0 else 0
            ox = x
            for lab, v in row:
                rw = (v * scale) / rh if rh > 0 else 0
                rects.append([lab, v, ox, y, rw, rh])
                ox += rw

    def lay(items, x, y, w, h):
        if not items or w * h <= 0:
            return
        scale = (w * h) / sum(v for _, v in items)
        row = []
        i = 0
        horiz = w >= h
        length = h if horiz else w
        while i < len(items):
            it = items[i]
            if not row:
                row = [it]
                i += 1
                continue
            if worst(row, length, scale) >= worst(row + [it], length, scale):
                row.append(it)
                i += 1
            else:
                place(row, x, y, w, h, horiz, scale)
                rs = sum(v for _, v in row) * scale
                if horiz:
                    x += rs / length
                    w -= rs / length
                else:
                    y += rs / length
                    h -= rs / length
                row = []
                rem = sum(v for _, v in items[i:])
                if rem > 0 and w * h > 0:
                    scale = (w * h) / rem
                horiz = w >= h
                length = h if horiz else w
        if row:
            place(row, x, y, w, h, horiz, scale)

    lay(items, x, y, w, h)
    return rects


def grid_and_legend(items, *, w: int = W, h: int = H):
    """[(label, value)] (already capped to ≤len(EMOJI)) → (body, legend) strings.

    `body` is the h×w emoji grid; `legend` maps each tile's emoji to its label and
    value. Callers prepend their own header line.
    """
    rects = squarify(items, 0, 0, w, h)
    idxmap = {lab: i for i, (lab, _) in enumerate(items)}
    grid = [[None] * w for _ in range(h)]
    for lab, v, rx, ry, rw, rh in rects:
        i = idxmap[lab]
        x0, y0 = int(round(rx)), int(round(ry))
        x1, y1 = int(round(rx + rw)), int(round(ry + rh))
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(w, x1)
        y1 = min(h, y1)
        if x1 <= x0:
            x1 = min(w, x0 + 1)
        if y1 <= y0:
            y1 = min(h, y0 + 1)
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                if grid[yy][xx] is None:
                    grid[yy][xx] = i
    for yy in range(h):
        for xx in range(w):
            if grid[yy][xx] is None:
                grid[yy][xx] = grid[yy][xx - 1] if xx > 0 else 0
    body = "\n".join("".join(EMOJI[grid[yy][xx]] for xx in range(w)) for yy in range(h))
    legend = "\n".join(f"{EMOJI[i]} {lab} · {human_mb(v)}" for i, (lab, v) in enumerate(items))
    return body, legend
