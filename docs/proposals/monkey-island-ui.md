---
type: proposal
implemented_by: []
tracking: 0
---

# Proposal — the Monkey Island UI: one style guide, every surface

The fleet's art direction already exists: the **MI1 style guide** Tommy posted on
discobots#20 ("Monkey Island (1990) Style Guide — ASCII/Emoji Translation"), whose
deterministic half shipped as `discokit.art` (density `RAMP`, Bayer `shade()`,
`Canvas`, the `melee_dock()` reference). This proposal is the combined, cross-repo
plan that grows it from *one reference scene* into the fleet's themed UI — and it
folds in the generative follow-on (the **MI1-ASCII LoRA** machine task on the dev
board) as its final phase, since the deterministic phases produce that model's
training data.

Art direction settled with Tommy (2026-07-04): **all Discord surfaces** are in
reach; the copy gets a **light MI voice** (captions and headers, nothing
load-bearing); scenes appear at **two moments** — the weekly digest header and
deploy / all-clear events; the **LoRA track is the last phase** of this plan.

> **Guardrails.** *Text-native, still* — scenes are strings in code blocks, never
> images (the PNG spike was shelved deliberately; #31's matplotlib hatch is an
> orthogonal, opt-in data-register escape). *Scenes are moments, not wallpaper* —
> one appears because something happened. *The status vocabulary is load-bearing* —
> `tokens.json` labels, colours and glyphs stay exactly as-is; MI voice lives in
> caption lanes only. *Degradable* — a failed scene render falls back to today's
> output, never blocks a post.

## The through-line

discokit speaks two registers: `graph.py` is the **data** register (btop: braille,
blocks, chips) and `art.py` is the **scene** register (MI1: ramps, dither, one
light source). The registers never mix inside a surface — data stays data. This
plan widens the scene register (a vocabulary, not a dock), gives scenes their
moments, threads one caption lane of MI voice across the fleet, then teaches a
local model to draw new scenes inside the same grammar.

## The phases

### Phase 1 — the scene vocabulary (`discobots` · discokit)
`art.py` grows a small registry beside `melee_dock()`: **`dock_dawn`** (the
all-clear sky) and **`ship_underway`** (a deploy leaving harbour), sharing the
band/sprite primitives; a **caption line** per scene (the light voice, always
*outside* the code block); and the style guide's mechanical rules land as test
asserts — grid ≤ 80×25, ≤ 7 ramp glyphs per scene, ≤ 2 emoji accents, no `▓▒░` /
box-drawing. Those asserts are the same **eval grammar** Phase 4 reuses as LoRA
fitness. **Checkpoint A:** the new scenes post to #ops round-style (desktop +
phone, like round D on #20) for Tommy's verdict before anything wires up.

### Phase 2 — the moments (`discobots` · ops)
The **weekly digest** (`ops/digest.py:270`) opens with a scene, its sky picked by
the week's health — calm night for a clean week, storm clearing after incidents.
The **autodeploy repaint** (`ops/autodeploy.sh:45`) follows a successful fleet
redeploy with `ship_underway`. The **watcher recovery** path
(`ops/watcher.py:143`) caps a recovery with `dock_dawn` — rate-limited to one
scene per recovery burst, and alert embeds themselves stay scene-free.
**Checkpoint B:** a week in situ, verdict before the voice spreads.

### Phase 3 — the light voice (`discobots` · every surface)
One caption lane per surface, outside every code block: the fleet-board title
line (`ops/fleet_status.py`), the wheel's idle line (`ops/loop_dashboard.py`),
the chat panel's answered-state summary (`ops/chat_dashboard.py`), the skills
spotlight intro (`ops/skills_discord.py:161`), a transit quiet-hours line
(`ops/transit_dashboard.py`). Status tokens, alert copy and labels: untouched.
**Checkpoint C:** the actual caption lines go to Tommy as a selectable prompt
before rollout.

### Phase 4 — the scene generator (`tommybot` + `discobots`)
Absorbs the **MI1-ASCII LoRA** machine task. Dataset: guide-constrained
(prompt → scene) pairs composed programmatically from the Phase-1 registry, plus
curated exemplars — the eval grammar filters every sample. Training: `mlx-lm`
LoRA on the Air (Metal stays off the mini). Integration: the tommybot#72
agent-first interface — `tommybot ask … --json` (or `--live` NDJSON), CLI shape
via `help --json`, adapter distributed with `tommybot bundle` — and every
generation re-validates against the grammar before it posts. Generations feed
the *same two moments* (variety for digest headers and all-clears), no new
surfaces. **Checkpoint D:** base-model and dataset-size calls (the task's open
questions) resolved with Tommy at phase kickoff.

## Fleet scan — every surface, its fit

| # | Surface | Today | MI fit (phase) |
| --- | --- | --- | --- |
| 1 | `ops/digest.py:270` | weekly summary embed | scene header + mood caption (**P2**) |
| 2 | `ops/autodeploy.sh:45` | repaints the #discobots board on merge | `ship_underway` on success (**P2**) |
| 3 | `ops/watcher.py:143` | "recovered" notify | `dock_dawn` cap on recovery (**P2**) |
| 4 | `ops/fleet_status.py` | pinned #ops board + `docs/fleet-status.md` | board title caption (**P3**); the wiki page stays unthemed |
| 5 | `ops/loop_dashboard.py` | the ferris wheel | idle caption only (**P3**) — the wheel is its own register |
| 6 | `ops/chat_dashboard.py` | tommybot thinking panel | answered-state caption (**P3**) |
| 7 | `ops/skills_discord.py:161` | skill spotlight | spotlight intro line (**P3**) |
| 8 | `ops/transit_dashboard.py` | live alerts panel | quiet-hours line only (**P3**) — alerts stay plain |
| 9 | `ops/ops_dashboard.py` | dev-status chips | no change — pure data register |
| 10 | `ops/embed_dashboard.py` | embeddings sync graph | no change — pure data register |
| 11 | `discokit/tokens.json` | the status palette | **untouched** (guardrail) |
| 12 | tommybot (#72 interface) | ask / live / bundle | LoRA adapter + scene gen (**P4**) |

**Affected repos:** `discobots` (P1–P3, P4 integration) and `tommybot` (P4
training + adapter). `obsidian-automations`: **none** — the MI dev-wiki skin was
considered and cut by art direction; the wiki keeps its current look and
`fleet-status.md` inherits no scenes.

## Relation to open work

- **#31 (matplotlib charts)** — an opt-in escape hatch for the *data* register;
  whatever its fate, the scene register stays text-native.
- **#23 / #21 (taste-training)** — voice-adjacent but separate: that plan learns
  the fleet's *prose* voice from reaction feedback; this one draws scenes. The
  eval-grammar/LoRA machinery is deliberately shaped so the two can share the
  MLX pipeline.
- **The MI1-ASCII LoRA machine task** (dev board) — superseded by Phase 4 when it
  starts; the task note gets updated to point here.

## Rollout

Each phase is one PR, independently shippable, degradable, and **gated on its
art checkpoint** — ambiguous taste calls resolve by asking Tommy, defaulting to
the narrower option. Order: **1 → 2 → 3 → 4**; Phase 1 can start on verdict A.
`implemented_by` tracks the PRs as they open.
