"""discokit.art — constraint-native ASCII scenes, per the MI1 style guide (PR #20).

Where graph.py is the fleet's *data* register (btop: braille, blocks, chips),
art.py is the *scene* register: Monkey Island (1990) discipline translated to
character cells. The constraints ARE the look:

    grid        ~60–80 × 20–25 cells, each cell a fat pixel — commit to it
    palette     a 5–7 glyph density ramp per scene (from RAMP, dark → light)
    shading     ordered (Bayer) dithering only — the visible texture is the
                aesthetic; a smooth blend reads as modern and wrong
    composition stacked tonal bands, background carries the mood, figures are
                small silhouettes, ONE light source radiating dithered falloff
    accents     emoji stay OUTSIDE the code block (title line), or on their own
                alignment lane — most render 2 cells wide and wreck the grid

Anti-patterns (deliberate absences): block elements ▓▒░ and box-drawing (that's
ANSI/DOS — graph.py's genre, not this one), smooth gradients, emoji mosaics.
"""

from __future__ import annotations

# The full density ramp, dark → light. Scenes SUBSET it (5–7 glyphs) — the
# restraint mirrors a 16-colour indexed palette.
RAMP = " .:-=+*#%@"

# 4×4 Bayer threshold matrix, normalised 0..1 — the only gradient tool here.
_BAYER4 = [
    [0, 8, 2, 10],
    [12, 4, 14, 6],
    [3, 11, 1, 9],
    [15, 7, 13, 5],
]


def shade(density: float, x: int = 0, y: int = 0, *, ramp: str = RAMP) -> str:
    """Map a 0..1 density to a ramp glyph, Bayer-dithered at the boundaries.

    The fractional part of the ramp position is resolved by the threshold
    matrix at (x, y), so neighbouring cells alternate glyphs in the ordered
    checker pattern instead of banding cleanly.
    """
    density = max(0.0, min(1.0, density))
    pos = density * (len(ramp) - 1)
    base = int(pos)
    frac = pos - base
    threshold = (_BAYER4[y % 4][x % 4] + 0.5) / 16
    idx = min(len(ramp) - 1, base + (1 if frac > threshold else 0))
    return ramp[idx]


class Canvas:
    """A fat-pixel grid. Paint density fields, overlay silhouettes, render."""

    def __init__(self, width: int = 64, height: int = 20) -> None:
        self.width = width
        self.height = height
        self.grid = [[" "] * width for _ in range(height)]

    def band(self, y0: int, y1: int, density: float, *, ramp: str = RAMP) -> None:
        """Fill rows [y0, y1) at one density — a tonal band, dithered."""
        for y in range(max(0, y0), min(self.height, y1)):
            for x in range(self.width):
                self.grid[y][x] = shade(density, x, y, ramp=ramp)

    def glow(self, cx: int, cy: int, radius: float, *, boost: float = 1.0,
             ramp: str = RAMP) -> None:
        """ONE light source: radial falloff added over what's painted.

        Additive — a cell's existing ramp level is raised toward the light,
        so a lantern brightens the dock planks it sits on.
        """
        for y in range(self.height):
            for x in range(self.width):
                # character cells are ~2× taller than wide; correct the ellipse
                d = ((x - cx) ** 2 + (2 * (y - cy)) ** 2) ** 0.5
                if d > radius:
                    continue
                lift = boost * (1 - d / radius) ** 2
                current = ramp.find(self.grid[y][x]) / (len(ramp) - 1)
                if current < 0:
                    current = 0.0
                self.grid[y][x] = shade(current + lift, x, y, ramp=ramp)

    def scatter(self, y0: int, y1: int, density: float, *, glyph: str = ".") -> None:
        """Sparse hand-scatter (stars, spray) — deterministic hash, NOT Bayer.

        Ordered dithering at very low density lights the same matrix cell per
        tile and reads as a periodic lattice; a hash scatter reads as sky.
        """
        for y in range(max(0, y0), min(self.height, y1)):
            for x in range(self.width):
                if (x * 2654435761 + y * 40503) % 1000 < density * 1000:
                    self.grid[y][x] = glyph

    def sprite(self, x0: int, y0: int, art: str) -> None:
        """Overlay a silhouette: non-space chars land, spaces are transparent."""
        for dy, row in enumerate(art.splitlines()):
            y = y0 + dy
            if not 0 <= y < self.height:
                continue
            for dx, ch in enumerate(row):
                x = x0 + dx
                if ch != " " and 0 <= x < self.width:
                    self.grid[y][x] = ch

    def render(self) -> str:
        return "\n".join("".join(row).rstrip() for row in self.grid)


def melee_dock(width: int = 64, height: int = 18) -> str:
    """The reference scene: Mêlée dock at night. One moon, one lantern.

    Doubles as the composition example — stacked bands (sky/sea/dock), a
    horizon dither seam, silhouette-first shapes, torchlit falloff.
    """
    c = Canvas(width, height)

    horizon = height // 2
    deck = height - 4
    moon_x = width - 11

    c.scatter(0, horizon, 0.04)                             # night sky: scattered stars
    c.band(horizon, horizon + 1, 0.5, ramp=" -.")           # horizon dither seam
    c.band(horizon + 1, deck, 0.22, ramp=" .:-")            # dark water, faint chop
    c.band(deck, height, 0.96, ramp=" #")                   # dock: solid silhouette mass
    c.band(deck, deck + 1, 0.85, ramp=" =")                 # lit plank edge on top

    # the moon glade: water catches the light in a dithered column
    for y in range(horizon + 1, deck):
        for x in range(moon_x - 2, min(width, moon_x + 6)):
            c.grid[y][x] = shade(0.42, x, y, ramp=" .:-=")

    # the moon — a solid hand-drawn disc (no gradient, no transparency)
    c.sprite(moon_x, 1, " .--.\n(@@@@)\n `--'")

    # a moored silhouette, outline-first (reads from its shape alone)
    c.sprite(width - 26, deck - 3, "   /|\n  /#|__\n |#####|")

    # the ONE light source: a lantern hung over the deck edge
    c.sprite(19, deck - 1, "@")
    c.glow(19, deck - 1, 10.0, boost=0.5)

    return c.render()
