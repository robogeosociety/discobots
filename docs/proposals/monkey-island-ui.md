---
type: proposal
implemented_by: [46]
tracking: 0
---

# Proposal — the Monkey Island UI: one style guide, every surface

The fleet's art direction already exists: the **MI1 style guide** Tommy posted on
discobots#20 ("Monkey Island (1990) Style Guide — ASCII/Emoji Translation"), whose
deterministic half shipped as `discokit.art` (density `RAMP`, Bayer `shade()`,
`Canvas`, the `melee_dock()` reference). This proposal is the combined, cross-repo
plan that grows it from *one reference scene* into the fleet's themed UI — Discord
**and** the dev wiki, one identity on both — and folds in the generative follow-on
(the **MI1-ASCII LoRA** machine task) as its final phase, since the deterministic
phases produce that model's training data.

Art direction settled with Tommy (2026-07-04, two rounds): **all Discord surfaces**
in reach; **light MI voice** (captions and headers, nothing load-bearing); Discord
scenes at **two moments** (weekly digest header, deploy / all-clear); the **dev
wiki joins in full** — the MI theme *replaces* the Matrix (1999) skin, **every
repo hub page** opens with a state-keyed scene, and the web moments are
**palette-cycling water + static dithered harbor backdrop + lantern hover glow**;
the **LoRA track is the last phase**.

> **Guardrails.** *Text-native, still* — scenes are strings (Discord code blocks,
> wiki `<pre>`), never images (#31's matplotlib hatch is an orthogonal, opt-in
> data-register escape). *Scenes are moments or state, not wallpaper.* *The status
> vocabulary is load-bearing* — `tokens.json` labels, colours and glyphs stay
> exactly as-is; on the web they arrive as the *generated* `tokens.css`, never
> hand-copied hexes. *Degradable* — a failed scene render falls back to today's
> output, and the wiki's JS moments decay to static CSS with JS off (as the
> Matrix rain does today).

## The through-line

discokit speaks two registers — `graph.py` (data: braille, blocks, chips) and
`art.py` (scene: ramps, dither, one light source) — and the registers never mix
inside a surface. The same discipline now spans two *interfaces*: Discord panels
and the dev wiki render from **one token source and one scene registry**, with
the wiki consuming them through the standard web stack the project already runs —
`wiki_render.py`'s Jinja layouts + theme CSS files. Styling concepts stop being
per-surface conventions and become code: tokens in `tokens.json`, scene grammar
in test asserts, chrome in CSS custom properties.

## The phases

### Phase 1 — the scene vocabulary (`discobots` · discokit)
`art.py` grows a small registry beside `melee_dock()`: **`dock_dawn`** (the
all-clear sky), **`ship_underway`** (a deploy leaving harbour), and a
**state-keyed picker** (`scene_for(state)` — quiet dock for stable, ship for
freshly deployed, storm clearing after incidents) that both Discord moments and
wiki hub pages will call; a **caption line** per scene (the light voice, always
outside the code block); and the style guide's mechanical rules land as test
asserts — grid ≤ 80×25, ≤ 7 ramp glyphs per scene, ≤ 2 emoji accents, no `▓▒░` /
box-drawing. Those asserts are the same **eval grammar** Phase 5 reuses as LoRA
fitness. **Checkpoint A:** the new scenes post to #ops round-style (desktop +
phone, like round D on #20) for Tommy's verdict before anything wires up.

### Phase 2 — the Discord moments (`discobots` · ops)
The **weekly digest** (`ops/digest.py:270`) opens with a scene, its sky picked by
the week's health. The **autodeploy repaint** (`ops/autodeploy.sh:45`) follows a
successful fleet redeploy with `ship_underway`. The **watcher recovery** path
(`ops/watcher.py:143`) caps a recovery with `dock_dawn` — rate-limited to one
scene per burst; alert embeds stay scene-free. **Checkpoint B:** a week in situ.

### Phase 3 — the light voice (`discobots` · every Discord surface)
One caption lane per surface, outside every code block: the fleet-board title
line (`ops/fleet_status.py`), the wheel's idle line (`ops/loop_dashboard.py`),
the chat panel's answered-state summary (`ops/chat_dashboard.py`), the skills
spotlight intro (`ops/skills_discord.py:161`), a transit quiet-hours line
(`ops/transit_dashboard.py`). Status tokens, alert copy and labels: untouched.
**Checkpoint C:** the actual caption lines go to Tommy as a selectable prompt.

### Phase 4 — the wiki theme (`obsidian-automations` · dev wiki)
The Matrix skin retires (archived live on the `theme/matrix` branch; main
strips at this phase's cutover) and `dev-wiki/theme/` gets its MI
successor on the same file contract (`automations/dev_wiki.py:86` `THEME_DIRS`):
**`mi.css`** — twilight palette as CSS custom properties over the shared base,
consuming discokit's **generated `tokens.css`** for every semantic colour
(status chips on hub pages match Discord embeds hex-for-hex, one source);
**`layout.html.j2`** — harbor chrome in place of rain-canvas + kana; and
**`water-cycle.js`** — the era's homage, a thin dithered water band whose
colours palette-cycle (matrix-rain.js's slot: deferred, decorative, empty
without JS). The static moments are pure CSS: the Bayer-textured twilight
backdrop (repeating gradients) and the amber lantern glow on link/heading hover.
**Every repo hub page** opens with a small state-keyed scene from the Phase-1
registry in a `<pre class="scene">` — `art.py` is stdlib-only, so it vendors
into `obsidian-automations` as a pinned copy with a drift test against discobots
(the committed-fleet-page pattern), keeping discokit canonical. The
`docs/fleet-status.md` page inherits the board's scene header for free.
**Checkpoint D:** side-by-side rounds — the #ops panel and the re-skinned wiki
page — before the theme cutover; typography (pixel-display vs clean mono
headings) decided from the round samples.

### Phase 5 — the scene generator (`tommybot` + `discobots`)
Absorbs the **MI1-ASCII LoRA** machine task. Dataset: guide-constrained
(prompt → scene) pairs composed from the Phase-1 registry plus curated
exemplars — the eval grammar filters every sample. Training: `mlx-lm` LoRA on
the Air (Metal stays off the mini). Integration: the tommybot#72 agent-first
interface — `tommybot ask … --json` / `--live`, adapter via `tommybot bundle` —
and every generation re-validates against the grammar before posting.
Generations feed the *same* moments and hub-scene slots, no new surfaces.
**Checkpoint E:** base-model and dataset-size calls at phase kickoff.

## Fleet scan — every surface, its fit

| # | Surface | Today | MI fit (phase) |
| --- | --- | --- | --- |
| 1 | `ops/digest.py:270` | weekly summary embed | scene header + mood caption (**P2**) |
| 2 | `ops/autodeploy.sh:45` | repaints the #discobots board on merge | `ship_underway` on success (**P2**) |
| 3 | `ops/watcher.py:143` | "recovered" notify | `dock_dawn` cap on recovery (**P2**) |
| 4 | `ops/fleet_status.py` | pinned #ops board + `docs/fleet-status.md` | board title caption (**P3**); the wiki page gets the scene header via **P4** |
| 5 | `ops/loop_dashboard.py` | the ferris wheel | idle caption only (**P3**) — the wheel is its own register |
| 6 | `ops/chat_dashboard.py` | tommybot thinking panel | answered-state caption (**P3**) |
| 7 | `ops/skills_discord.py:161` | skill spotlight | spotlight intro line (**P3**) |
| 8 | `ops/transit_dashboard.py` | live alerts panel | quiet-hours line only (**P3**) — alerts stay plain |
| 9 | `ops/ops_dashboard.py` / `ops/embed_dashboard.py` | data-register panels | no change — pure data register |
| 10 | `discokit/tokens.json` → `build_tokens.py` | status palette → embed ints + `tokens.css` | source stays; `tokens.css` becomes the wiki's semantic palette (**P4**) |
| 11 | `automations/dev_wiki.py:86` + `dev-wiki/theme/` | Matrix theme (matrix.css, layout.html.j2, matrix-rain.js) | replaced by `mi.css` + harbor layout + `water-cycle.js` (**P4**) |
| 12 | dev-wiki repo hub pages (`automations/dev_wiki.py`) | text hubs | state-keyed scene header from vendored `art.py` (**P4**) |
| 13 | tommybot (#72 interface) | ask / live / bundle | LoRA adapter + scene gen (**P5**) |

**Affected repos:** `discobots` (P1–P3, P5 integration), `obsidian-automations`
(P4), `tommybot` (P5). The camping/travel wikis (Quartz stack) and rgs-wiki keep
their own looks — this theme is the *dev* wiki's identity.

## Relation to open work

- **#31 (matplotlib charts)** — an opt-in escape hatch for the *data* register;
  the scene register stays text-native regardless.
- **obsidian-automations #168 (Matrix redesign)** — superseded by P4; the theme
  file contract it established (theme dir + css overlay + layout + one JS moment)
  is exactly what the MI skin steps into.
- **#23 / #21 (taste-training)** — separate: that plan learns the fleet's *prose*
  voice from reaction feedback; this one draws scenes. Both shape the same MLX
  pipeline.
- **The MI1-ASCII LoRA machine task** (dev board) — superseded by Phase 5 when it
  starts; the task note gets updated to point here.

## Rollout

Each phase is one PR in its home repo, independently shippable, degradable, and
**gated on its art checkpoint** — ambiguous taste calls resolve by asking Tommy,
defaulting to the narrower option. Order: **1 → 2 → 3 → 4 → 5** (P4 needs only
P1, so it may run parallel to P2/P3 if scheduling favours it; the cutover still
waits for checkpoint D). `implemented_by` tracks the PRs as they open.
