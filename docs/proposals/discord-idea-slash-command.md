---
type: proposal
implemented_by: []
tracking: 163
---

# Proposal — Discord slash commands that feed the board (`/idea` + proposal feedback)

Today "the board" ([Projects v2 #7](https://github.com/orgs/robogeosociety/projects/7))
is fed from the desk — you open an Idea issue in a browser, land it on the Ideas
column, comment on proposals in the GitHub UI. But half the ideas arrive on a
phone, in Discord, mid-conversation. This proposal closes that gap with two
Discord slash commands that write straight to GitHub: `/idea` opens an Idea
issue on the board, and `/feedback` comments on an open proposal/PR — optionally
`@claude`-mentioning it so the canonical responder acts on the note.

## Problem Statement

The board's intake is desk-bound. `discobots` already carries GitHub activity
**out** to Discord (`ops/github_discord.py`, the #dev heartbeat), but nothing
flows **in**: there is no way to create an Idea issue, land it on Project #7, or
leave proposal feedback without leaving Discord for a browser. Ideas that surface
in chat (from a phone, from a web research session pasted into #dev) get lost
before they reach the Ideas column. The master Idea (robot-geographical-society#163)
explicitly requires Discord slash commands — `/idea`, proposal/PR feedback — as a
first-class intake path, and requires that every Idea land on the board from a
template.

## Requirements

- [ ] `/idea "<text>"` opens a GitHub **Issue** in the org from the Idea template
      (`## Problem statement` / `## Use cases` / `## Requirements`) and lands it on
      Project #7 in the **Ideas** column.
- [ ] `/feedback <pr-url-or-#> "<text>"` posts a comment on an open proposal/PR;
      an optional `claude:true` flag prefixes `@claude` so the canonical responder
      (`.github/workflows/claude.yml`) picks it up and edits the proposal.
- [ ] Runs on the **mini supervisor fleet** as one more managed unit — a gateway
      bot client (the first `discobots` unit that *reads* Discord), not a webhook
      poster.
- [ ] Reuse GitHub built-ins first (`gh` CLI / REST + `gh project item-add`), and
      prior art: the `obsidian-automations` `github_tasks` collector's `gh api
      graphql` issue queries, and `discobots`' existing `gh auth token` path.
- [ ] Discord→GitHub **identity**: map the invoking Discord user to a trust
      decision; only allowlisted users may write; the issue records who asked.
- [ ] Python-first; degrade safely (GitHub down → ephemeral error reply, no crash).

## Solution

**A discord.py slash-command gateway, modeled on `ask-dash`.** The registry
already documents `ask-dash` — a `/ask` slash-command gateway bot
(`observability-config`) gated by `DISCORD_ALLOWED_USER_IDS`. This proposal adds a
sibling **board gateway** in `discobots/ops/` (`board_gateway.py`), a long-running
`discord.py` client registering two guild slash commands. It is the first
`discobots` unit that holds a gateway connection and reads Discord, so it follows
the registry's hard rule for read-side bots (own app, own token, scoped intents).

```
/idea "<text>"          → gh issue create --template idea.yml (org repo)
                        → gh project item-add 7 --url <issue>
                        → (project default Status = Ideas; else set field)
                        → ✅ ephemeral reply with the issue URL

/feedback #NNN "<text>" → gh pr comment / gh api …/issues/NNN/comments
   claude:true          → body prefixed "@claude " so claude.yml fires
                        → ✅ ephemeral reply with the comment URL
```

**Issue creation** reuses the `github_tasks` pattern (subprocess `gh api` /
`gh issue create`) rather than a new HTTP client. The **Idea template** is the org
template the master Idea defines; if the org template PR (`.github`) has not landed
yet, the gateway ships the three-heading body inline and switches to `--template`
once present. **Board placement** uses `gh project item-add --owner robogeosociety
7`; the Ideas column is Project #7's default Status, matching the project-sync rule
"open Issue → Ideas".

**Identity & auth.** The gateway authenticates to GitHub as one machine identity
(`gh auth token` on the mini, same as `github_discord.py`) — not as the Discord
user. Discord identity is an **authorization** gate, not GitHub impersonation: an
allowlist (`DISCORD_ALLOWED_USER_IDS`, exactly as `ask-dash` does) decides who may
write, and the created issue's body footer records `requested-by: <discord
handle>` for provenance. This keeps one auditable GitHub actor while attributing
intent. A paid **GitHub Teams** plan would let each contributor carry their own
seat/identity so issues could be authored *as* them; noted as a future upgrade, not
required for v1.

**Where it runs.** One more unit on the **mini supervisor fleet** — an OrbStack
container managed exactly like the eight `ops/` bots (`just up board-gateway`),
secrets injected at `docker run` from the host `.env`, registered in `DISCORD.md`.
Unlike the poster bots it needs the gateway intents and a persistent connection, so
it runs as a daemon, not a cron unit.

## Alternatives

- **GitHub-hosted intake only (no Discord).** Issue Forms already give a templated
  Idea on the web. Rejected: #163 explicitly wants a Discord path, and the value is
  capturing ideas *where they surface* — in chat, on a phone.
- **Webhook + Discord "Interactions endpoint" (no gateway bot).** Discord can POST
  slash-command interactions to an HTTPS endpoint, reusing the webhook-receiver
  ingress the valkey-expansion proposal (discobots, `docs/proposals/valkey-expansion.md`)
  already scopes. Viable and stateless, but needs a public verified ingress and
  Ed25519 request-signature checking; a gateway bot on the always-on mini is simpler
  for v1. Kept as the scale-out path.
- **Extend `tommybot` (already a live gateway) with the commands.** Rejected by the
  registry's **bot-per-purpose** rule — board writes are a distinct purpose from the
  RAG bot and deserve their own app/token/allowlist.
- **`@claude` in a fresh issue instead of `/feedback`.** Good for net-new work, but
  `/feedback` targets an *existing* proposal/PR; both paths coexist.

## Tasks

- [ ] Add `ops/board_gateway.py` — `discord.py` client, two guild slash commands,
      `DISCORD_ALLOWED_USER_IDS` allowlist gate, ephemeral replies.
- [ ] `/idea`: `gh issue create` (Idea template or inline body) + `gh project
      item-add 7`; append `requested-by:` provenance footer.
- [ ] `/feedback`: resolve PR ref, `gh pr comment`; `claude:true` → prefix `@claude`.
- [ ] Reuse a `github_tasks`-style `gh api` helper; no new HTTP dependency.
- [ ] Package as an OrbStack unit (`ops/docker/board-gateway/`), a `justfile`
      recipe, and secret injection via `ops/run.sh`.
- [ ] Register the new bot/app + token + intents in `DISCORD.md` (registry of record).
- [ ] Tests under `ops/tests/` (allowlist gate, body assembly, dry-run — no live post).
- [ ] Follow-up note: GitHub Teams seats for per-user issue authorship.

## Further Reading

- Master Idea: robot-geographical-society#163 (`/idea` + proposal-feedback requirement, board columns)
- Board: [Projects v2 #7](https://github.com/orgs/robogeosociety/projects/7); project-sync rule "open Issue → Ideas"
- Prior art — issue queries: `obsidian-automations/weekly/collectors/github_tasks.py`
- Prior art — GitHub→Discord + `gh auth token`: `ops/github_discord.py`, `ops/discokit/`
- Prior art — slash-command gateway w/ allowlist: `ask-dash` (`observability-config`), `DISCORD.md`
- Canonical `@claude` responder: `.github/workflows/claude.yml`
- Registry of record + read-side bot rule: `DISCORD.md`
- Scale-out ingress (webhook interactions): `docs/proposals/valkey-expansion.md`
