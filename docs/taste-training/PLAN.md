# docs/taste-training/PLAN.md — a scheduled agent that trains the fleet's voice

Handoff spec for whoever (human or agent) picks up
[discobots#21](https://github.com/tommyroar/discobots/issues/21). Not implemented yet — this
is the plan a future Claude Code session should read before writing code.

## Why

The fleet's dashboards settled on Discord-native text — emoji glyphs, braille/ASCII charts,
edit-in-place refreshes (`ops/discokit/graph.py`, and its sibling `art.py` for the scene
register). The best of it is hand-rolled and taste-driven, and that taste should compound:
every 👍/👎 Tommy gives a notification is a labeled example of what the voice should sound
like next time. This plan turns that feedback into a recurring LoRA fine-tune, reusing
tommybot's existing training pipeline rather than building a new one.

**Scope note:** there's already a "MI1-ASCII LoRA" machine task tracked on the dev board,
scoped to teaching a model the `discokit.art` scene grammar specifically (density ramps,
Bayer dither, evaluated against the MI1 style guide). That's a narrower, generative-art
effort. This plan is the broader one: a *scheduled, feedback-driven* retraining loop covering
the whole notification voice, not just ASCII scenes. Check for overlap before building both.

**Governing constraint, inherited from the Phase-3 pivot: no image generation, ever.** The
model's job is to emit *text* — glyphs, structure, phrasing — never to render pixels.

## What already exists — reuse, don't rebuild

**tommybot's LoRA pipeline is production-ready and directly reusable.** `~/dev/tommybot/`:
- `TRAINING.md`, `docs/lora-training.md`, `adapters/README.md`, `tommybot/train.py` — MLX QLoRA
  against the 4-bit Qwen3 base (`qwenbot-qwen3-8b-4bit` per `.env`), trained via
  `mlx_lm.lora`.
- **Entry point:** `just train <vault> <data-dir>` → `python -m tommybot.train <vault> --data
  <data-dir> --iters 600`. Output lands at `adapters/<vault>/adapters.safetensors`, auto-
  discovered by `tommybot.agents.resolve_adapter()` — deploying a new adapter is zero code
  change.
- **Data format:** JSONL chat examples, `{system, retrieved-context, question} → ideal answer`,
  split `train.jsonl` / `valid.jsonl`, mlx-lm chat format, "a few hundred examples" per the docs.
- **Safety gate:** `qwengen verify --adapter <path> --target <profile>` — an OOM guard on the
  fused model before anything ships.
- **Explicit design stance (TRAINING.md): fine-tuning is for voice/format only, never facts.**
  This plan's corpus must respect that — see Guardrails below.

**discokit is ground truth for the taxonomy.** `ops/discokit/tokens.py` (generated from
`tokens.json`) defines the six-state glyph+dot+label vocabulary; `ops/discokit/graph.py`
defines the braille/spark/bar/chip primitives. Any model output has to be checked against
these, not treated as free-form generation — see Guardrails.

**obsidian-automations has reusable note-iteration, and a precedent worth copying wholesale.**
`~/dev/obsidian-automations/`:
- `lib/vault.py` / `lib/vaults.py` — generic `read_note`/`write_note`, frontmatter split,
  filtered recursive `.md` iteration. Reusable as-is if this plan ever samples Tommy's own
  vault prose as an auxiliary voice signal (no code here isolates human-authored notes from
  automation-generated ones — that filter would be net-new).
- `weekly/TONE.md` + `weekly/lib/claude.py::prompt()` (`claude -p --append-system-prompt`) —
  an existing "house voice" spec injected as a system-prompt overlay, not baked into weights.
  **This is the cheap baseline to try before investing in a fine-tune** — see Phase T-1.

**What's genuinely missing, fleet-wide: no chat-log storage, no reaction/feedback capture,
anywhere.** `tommybot/bot.py::on_message` is stateless (nothing persisted); no webhook bot in
`ops/` can read messages or reactions at all (webhooks are POST-only). This is the one piece
with no existing infra to lean on.

## Phase T-1 — try the cheap thing first

Before scheduling any training run: hand-write a `TONE.md`-equivalent for the dashboard voice
(a handful of calibration examples + the hard rules already implicit in `discokit.graph`'s
docstring — glyphs outside code blocks, monospace inside, widths under ~30 chars) and see how
far prompting alone gets a resident qwenbot. If that's good enough, the fine-tune in T0–T4
may not be worth building at all. Only proceed past this phase if few-shot prompting visibly
falls short of the taste bar.

## Phase T0 — feedback capture (the net-new piece)

A listener that records reactions on the fleet's own notification messages: `(message_id,
channel, rendered_text, state_snapshot_if_recoverable, emoji, reactor_id, ts)` to a local
store — **not committed to the repo** (personal behavioral data, same discipline as
`.env`/secrets: local-only, gitignored). Needs a live Discord gateway connection, which no
webhook bot has. See Open Decision 1 before building this — where it lives is not obvious.

## Phase T1 — corpus store

Append-only JSONL, one record per labeled example: `{state: <the dict build_panel() takes>,
rendered: <the exact string posted>, signal: "liked"|"disliked", source: "notification",
ts}`. Optionally widened with vault-prose emoji samples (`source: "vault"`) per Tommy's
original ask — lower-confidence signal (usage, not explicit approval), weight accordingly.

## Phase T2 — corpus → training data

Transform T1 records into tommybot's JSONL chat format: `question` = the state snapshot
(serialized), `system` = the taxonomy + width rules (the T-1 spec, if written), `answer` =
`rendered` for `liked` examples only. **Disliked examples don't become negative training
data in this format** — mlx-lm's supervised format has no contrastive/DPO mode here; treat
`disliked` as a signal to *exclude* the pattern from future generation, tracked separately,
not as trainable rows. (A DPO-style approach is a valid future upgrade, out of scope for v1.)

## Phase T3 — the scheduled run

A cron-triggered step (`schedule` skill, or a supercronic job if it can run where MLX lives):
1. Check corpus growth since last training — skip the run below a minimum new-example
   threshold (avoid retraining on noise; exact number is an Open Decision).
2. `just train taste <data-dir>` (adapter name `taste`, sibling to tommybot's other
   per-vault adapters).
3. `qwengen verify --adapter adapters/taste/adapters.safetensors --target <profile>`.
4. Eval the new adapter against a small held-out set: glyph-taxonomy correctness (every
   glyph emitted is a real `tokens.BY_KEY` member), monospace alignment (braille/bar output
   is fixed-width), Discord's 4096-char cap. Compare to the currently-deployed adapter.
5. Promote only on non-regression. Post a summary card to #ops — text-native, via
   `discokit.graph` — showing corpus growth and the eval delta.

## Guardrails (apply throughout, not just at the end)

- **Voice, not facts** (TRAINING.md's rule) — the corpus is glyphs/structure/phrasing, never
  factual content about services/vaults/whatever the dashboard is reporting on.
- **The taxonomy is not learned, it's checked.** A generated glyph that isn't in
  `tokens.BY_KEY` is a bug, not a creative choice — validate-and-retry (mentioned in #21),
  don't trust raw generation for anything meaning-bearing.
- **guard.py scope** — any code that reads messages stays inside `guard.is_own_guild()`,
  same as every other Discord-reading piece of this fleet.
- **Determinism for the hash-diff.** `discokit.Dashboard.tick()` only edits on real content
  change; a model sampled at temperature > 0 breaks that unless output is cached per-state
  or temperature is pinned near 0.

## Open decisions — pick before Phase T0 starts

1. **Where does the reaction listener live?** Candidates: piggyback on the `discord-ops`
   channel session (already has gateway access, sits in #ops); a narrow new listener on
   `discokit.live`'s asyncio loop (pulls Phase-4 gateway work forward, narrowly scoped); wait
   for the fleet-hosting plan's Claude router (obsidian-automations#149, F2) so there's only
   ever one new gateway connection, not two. Recommend deferring to whichever lands first
   rather than building a third gateway bot.
2. **Where does training actually run?** MLX/Metal means the Air or the mini — the mini
   already OOM'd a similar workload, and the Air already hosts tommybot's resident MLX serve;
   a scheduled training run needs to not collide with either. Needs an explicit schedule
   window, not just "whenever cron fires."
3. **What counts as signal?** 👍/👎 only, or also replies/corrections? Keep v1 narrow
   (reactions only) unless there's a reason not to.
4. **Cold-start threshold** — how many liked examples before the first training run fires.
   tommybot's own docs suggest "a few hundred" is the target size; early runs will be far
   below that, so T3 step 1's threshold should probably gate on a much smaller number
   (dozens) with the understanding that early adapters are weak, or skip training entirely
   below some floor and just keep accumulating.
5. **Inference role** — full generation of a dashboard's text, or the model only picks
   flavor (which synonym, which footer phrasing) while `discokit.graph`'s deterministic
   renderer stays the structural backbone? The determinism guardrail above argues for the
   latter, at least for v1.

## Non-goals

- No image/pixel generation (governing constraint, not up for debate — see the shelved card
  renderer, discobots#19).
- No touching RAG/factual content or tommybot's existing vault adapters.
- No new Discord bot identity if an existing one (OpsBot) can carry the listener.

## First PR, if this gets picked up

Phase T-1 alone (a hand-written tone spec + a prompting experiment) is a same-day, low-risk
slice that answers whether Phases T0–T4 are worth building at all. Start there.
