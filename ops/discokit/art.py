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

    def glow(self, cx: int, cy: int, radius: float, *, boost: float = 1.0, ramp: str = RAMP) -> None:
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

    c.scatter(0, horizon, 0.04)  # night sky: scattered stars
    c.band(horizon, horizon + 1, 0.5, ramp=" -.")  # horizon dither seam
    c.band(horizon + 1, deck, 0.22, ramp=" .:-")  # dark water, faint chop
    c.band(deck, height, 0.96, ramp=" #")  # dock: solid silhouette mass
    c.band(deck, deck + 1, 0.85, ramp=" =")  # lit plank edge on top

    # the moon glade: water catches the light in a dithered column
    for y in range(horizon + 1, deck):
        for x in range(moon_x - 2, min(width, moon_x + 6)):
            c.grid[y][x] = shade(0.42, x, y, ramp=" .:-=")

    # the moon — a solid hand-drawn disc (no gradient, no transparency)
    c.sprite(moon_x, 1, " .--.\n(@@@@)\n `--'")

    # a moored silhouette, outline-first (reads from its shape alone)
    c.sprite(width - 26, deck - 3, "   /|\n  /#|__\n |#####|")

    # the ONE light source: a lantern hung over the deck edge (the glow keeps
    # to a 7-glyph subset — the grammar's ramp budget holds for the reference)
    c.sprite(19, deck - 1, "@")
    c.glow(19, deck - 1, 10.0, boost=0.5, ramp=" .:-=+#@")

    return c.render()


def dock_dawn(width: int = 64, height: int = 18) -> str:
    """The all-clear scene: the same dock at first light. One sun, no stars.

    Inverts the night reference — the sky BRIGHTENS toward the horizon, the
    water carries a sun glade instead of a moon glade, and the dock reads
    contre-jour: a dark mass against the light it waited for.
    """
    c = Canvas(width, height)

    horizon = height // 2
    deck = height - 4
    sun_x = width - 11

    c.band(0, 3, 0.05, ramp=" .:")  # high sky, still night-thin
    c.band(3, 6, 0.14, ramp=" .:")  # the gradient turns over
    c.band(6, horizon, 0.28, ramp=" .:-")  # glow gathering low
    c.band(horizon, horizon + 1, 0.55, ramp=" -=")  # horizon seam, already lit
    c.band(horizon + 1, deck, 0.2, ramp=" .:-")  # morning water, small chop
    c.band(deck, height, 0.96, ramp=" #")  # dock: contre-jour mass
    c.band(deck, deck + 1, 0.85, ramp=" =")  # plank edge catching light

    # the sun glade: the water brightens in a dithered column under the disc
    for y in range(horizon + 1, deck):
        for x in range(sun_x - 2, min(width, sun_x + 6)):
            c.grid[y][x] = shade(0.5, x, y, ramp=" .:-=")

    # dawn radiates from where the sun will sit (glow first, disc last, so the
    # hand-drawn disc stays pristine and the ramp census stays a 7-subset)
    c.glow(sun_x + 2, horizon - 1, 13.0, boost=0.5, ramp=" .:-=+#@")

    # two strokes of morning traffic — gulls read from outline alone
    c.sprite(14, 2, "v")
    c.sprite(22, 3, "v")

    # the sun — a solid disc clearing the horizon (an @-crown, not ramp dots:
    # a dotted rim would dissolve into the sky dither it rises through)
    c.sprite(sun_x, horizon - 2, " @@@@\n(@@@@)")

    return c.render()


def ship_underway(width: int = 64, height: int = 18) -> str:
    """The deploy scene: a ship pulling out of harbour on the night tide.

    The dock shrinks to a stub at the frame's edge (what she's leaving); the
    ship is the register's largest silhouette — still outline-first, lit by
    ONE stern lantern, trailing a dithered wake back toward the dock.
    """
    c = Canvas(width, height)

    horizon = height // 2
    hull_top = horizon + 1
    hull_bottom = height - 4
    ship_x = width // 2 - 4

    c.scatter(0, horizon, 0.04)  # night sky, scattered stars
    c.band(horizon, horizon + 1, 0.5, ramp=" -.")  # horizon dither seam
    c.band(horizon + 1, height, 0.2, ramp=" .:-")  # open water
    for y in range(hull_bottom + 1, height):  # the dock stub she's leaving
        for x in range(0, 7 - 2 * (y - hull_bottom - 1)):
            c.grid[y][x] = "#"

    # the wake: a dashed seam back toward the dock, fading with distance
    for x in range(8, ship_x + 2):
        c.grid[hull_bottom + 1][x] = shade(0.72 - (ship_x - x) / 55, x, hull_bottom + 1, ramp=" -")

    # lanternlight on the water FIRST — the ship overlays it as pure silhouette
    # (glow repaints non-ramp glyphs, so it must never run over the rigging)
    c.glow(ship_x + 3, hull_top + 3, 9.0, boost=0.5, ramp=" .:-=+#@")

    # the ship — mast, sail, hull; bow to open water, outline-first
    c.sprite(
        ship_x, hull_top, "      |\\\n      |#\\\n      |##\\\n \\____|###\\_\n  \\########/\n   `------'"
    )

    # the ONE light source: the stern lantern, hung on the aft rail
    c.sprite(ship_x + 3, hull_top + 3, "@")

    return c.render()


# ── the scene register (Phase 1 of the Monkey Island UI proposal) ────────────
# One caption line per scene — the proposal's "light MI voice". Captions ride
# OUTSIDE the code block (they may carry diacritics; scenes stay pure ASCII),
# and the status vocabulary never appears in them.
CAPTIONS = {
    "melee_dock": "A quiet night on Mêlée — the fleet sleeps sound.",
    "dock_dawn": "Dawn over the dock — all clear on the morning tide.",
    "ship_underway": "Fresh code on the tide — she's underway.",
}

SCENES = {
    "melee_dock": melee_dock,
    "dock_dawn": dock_dawn,
    "ship_underway": ship_underway,
}

# ── the mechanical grammar (the style guide's checkable rules) ───────────────
# Phase-4 LoRA fitness reuses THIS function: a generated scene must pass
# check() before it may post. The thresholds ARE the guide: grid bounds, a
# ramp restricted like a 16-colour palette, a two-accent emoji budget, and the
# banned sibling registers (ANSI/DOS block + box-drawing belong to graph.py).
_BANNED = set("▓▒░█▄▀▌▐│─┌┐└┘├┤┬┴┼║═╔╗╚╝╠╣╦╩╬")
MAX_COLS = 80
MAX_ROWS = 25
MAX_RAMP_GLYPHS = 7
MAX_EMOJI = 2


def check(scene: str) -> list[str]:
    """Style-guide violations for a rendered scene — [] means guide-compliant."""
    problems: list[str] = []
    lines = scene.splitlines()
    if len(lines) > MAX_ROWS:
        problems.append(f"{len(lines)} rows exceeds {MAX_ROWS}")
    width = max((len(line) for line in lines), default=0)
    if width > MAX_COLS:
        problems.append(f"{width} cols exceeds {MAX_COLS}")
    chars = set(scene) - {" ", "\n"}
    banned = chars & _BANNED
    if banned:
        problems.append("banned block/box glyphs: " + "".join(sorted(banned)))
    ramp_used = chars & set(RAMP)
    if len(ramp_used) > MAX_RAMP_GLYPHS:
        problems.append(
            f"{len(ramp_used)} ramp glyphs ({''.join(sorted(ramp_used))}) exceeds {MAX_RAMP_GLYPHS}"
        )
    emoji = [ch for ch in chars if ord(ch) >= 0x2600]
    if len(emoji) > MAX_EMOJI:
        problems.append(f"{len(emoji)} emoji accents exceeds {MAX_EMOJI}")
    return problems
