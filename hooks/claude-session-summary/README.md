# claude-session-summary — #dev session digests

Posts a summary of every substantial Claude Code session to **#dev**
(channel `1480240774879904096`), in the house style of the hand-written
migration digests (emoji + bold title, tight changelog prose, `-#` footer
with host/cwd/duration). Summaries are written by a headless
`claude -p` call (Haiku by default) over a condensed transcript, then
posted via the `DISCORD_WEBHOOK_GITHUB` webhook (the #dev channel, né
#github) with a `tommyroar` username override.

## Deployment (Air + mini, installed 2026-07-21)

Per machine:

| Piece | Location |
| --- | --- |
| Script | `~/.claude/hooks/dev-session-summary.py` (copy of `session_summary.py` here) |
| Secret | `~/.claude/hooks/dev-summary.env` — `DISCORD_WEBHOOK_DEV=…`, sourced from the canonical webhook store (mini `~/dev/observability/grafana/.env`, key `DISCORD_WEBHOOK_GITHUB`) |
| Instant path | `SessionEnd` hook in `~/.claude/settings.json` (`async`, 300 s timeout) |
| Backstop | launchd `com.claude.dev-summary-sweep` — daily sweep (Air 21:15, mini 21:45) catching sessions that never fired SessionEnd (crashes, killed terminals) |
| Dedup state | `~/.claude/hooks/dev-summary-posted.json` — session ids already posted, shared by hook + sweep; pre-seeded with all pre-rollout sessions so the first sweep didn't flood the channel |

## Behavior

- **Substantial only**: ≥1 human prompt and ≥6 assistant turns; quick Q&A
  sessions and empty sessions are skipped.
- **No recursion**: the summarizer's own `claude -p` run sets
  `DEV_SUMMARY_SKIP=1`, which the hook honors.
- Sweep window: sessions idle ≥45 min and ≤3 days old.
- `python3 session_summary.py post <transcript.jsonl>` force-posts one
  session (testing).

## Claude Code web sessions (`cloud-hook` mode)

Web sessions (claude.ai/code) run in cloud sandboxes that never see the
user-level pipeline, but they DO run this repo's committed
`.claude/settings.json` — which registers
`session_summary.py cloud-hook` as a SessionEnd hook. That mode:

- **no-ops when `~/.claude/hooks/dev-summary.env` exists** (i.e. on the
  Air/mini, where the user-level hook already handles the session) so
  local sessions in this repo never double-post;
- reads `DISCORD_WEBHOOK_DEV` from the cloud environment's configured
  env vars (there is no secrets store yet — the value is visible to
  anyone who can edit the environment);
- tries `claude -p` and **falls back to a plain digest** (title, first
  prompt, model, `claude-web` footer) if the CLI isn't in the sandbox.

One-time setup per cloud environment (Tommy, in the claude.ai UI):
set `DISCORD_WEBHOOK_DEV` in the environment's env vars and make sure
network access allows `discord.com`.
