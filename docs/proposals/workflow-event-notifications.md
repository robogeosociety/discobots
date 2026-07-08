---
type: proposal
implemented_by: []
tracking: 163
---

# Proposal — Workflow-event notifications to #dev and the dev-wiki

The #dev heartbeat already narrates raw GitHub activity — PRs opened, issues,
releases, red CI. What it does **not** narrate is the board's own state machine:
when a proposal moves to *ready to build*, when a PR merges into a deployment,
when a release cuts. Those are the moments a human wants pushed to them, phrased
as workflow steps ("Proposal X moved to ready — ready to build"), not as raw API
events. This proposal adds a **workflow-event layer** on top of the existing feed
routing, fanning the same events to **#dev** (Discord) and the **dev-wiki**.

## Problem Statement

The board (robot-geographical-society#163, [Projects v2 #7](https://github.com/orgs/robogeosociety/projects/7))
tracks work cradle-to-grave across Ideas → Proposals → Drafts → Done, but its
*transitions* are invisible. `ops/github_discord.py` posts each raw org event
once (PR opened, issue labeled, release), and `obsidian-automations` builds the
dev-wiki, but neither says "this proposal is now ready to build" or "this PR
merged into a deployment." #163 requires exactly this: *"Extend Discord +
dev-wiki notifications with workflow events ('Proposal X is ready to build')."*
The signal exists in GitHub (PR ready-for-review, merge, project Status change,
release/deployment) — it just is not shaped into a workflow narrative or routed
to the two surfaces that already exist.

## Requirements

- [ ] Emit a **workflow-event** notification when board/workflow state changes:
      proposal moved to ready (draft→ready-for-review), PR merged, deployment
      created, release published.
- [ ] Route each to **#dev** (Discord webhook) **and** the **dev-wiki** (a
      workflow-events feed page / changelog entry).
- [ ] Reuse `discobots`' existing feed routing (`ops/discokit/` — `config.webhook`,
      `poster.Poster`, `notify.ChangeFeed` post-once gate) — do not re-implement
      posting or dedup.
- [ ] Reuse `obsidian-automations`' dev-wiki render pipeline for the wiki surface.
- [ ] Define **event sources** explicitly (GitHub webhooks/Actions vs project-sync
      reconciliation) and the **message shape** per event type.
- [ ] GitHub built-ins first; Python-first; degrade safely (a surface down → log
      and continue, never block the other).

## Solution

**A workflow-events emitter that classifies GitHub signals into board
transitions, then fans out through the existing routers.** The hard part is not
transport — `discokit` already posts and dedups, the dev-wiki already renders —
it is *recognizing a transition* and *phrasing it*. So the new code is a thin
classifier + a fan-out; both surfaces are reuse.

**Event sources — two rails, matched to what each can see.**

| Transition | Source | Why |
| --- | --- | --- |
| Proposal → ready ("ready to build") | `pull_request` `ready_for_review` (draft→ready) | GitHub emits it directly; a ready PR = a Proposal per #163 |
| PR merged | `pull_request` `closed`+`merged` | native event |
| Deployment / release created | `deployment` / `release` events | native events |
| Board **Status** field change (Ideas→Proposals→Done) | **project-sync** reconciliation | Project v2 field moves are **not** in the org events feed — project-sync (robot-geographical-society#161, `.github` PR #17) is the only place that reads Status |

The first three ride the **GitHub-native rail**: the pragmatic v1 extends
`github_discord.py`'s existing 30-min `gh api` scan to classify these event types
into workflow notifications (it already fetches releases and PR events). The
instant path — an HMAC webhook receiver → a `fleet.workflow.event` bus topic — is
already scoped in `docs/proposals/valkey-expansion.md` (webhook receiver →
`fleet.github.event`); this proposal is a first consumer of that event, so it lands
poll-first and upgrades to push when that ingress ships. The fourth transition
(**project Status**) has no webhook, so it is emitted by **project-sync** when it
moves an item's Status — the only component that reads Project v2 fields.

**Message shape.** One internal envelope, rendered per surface:

```json
{ "kind": "proposal_ready" | "pr_merged" | "deploy" | "release" | "status_move",
  "repo": "robogeosociety/discobots", "number": 50,
  "title": "The board grows a Discord front door",
  "url": "https://github.com/robogeosociety/discobots/pull/50",
  "from": "Drafts", "to": "Proposals", "actor": "tommyroar", "ts": 1782971016.4 }
```

- **Discord (#dev):** a `discokit` embed — palette by kind (merge purple, release
  purple, ready = healthy-green), one line of narrative ("Proposal *X* moved to
  ready — ready to build"), title-linked. Deduped by the `ChangeFeed` post-once
  gate on `kind:repo:number:to`.
- **dev-wiki:** the same envelope appended to a **workflow-events** feed the
  dev-wiki render consumes (alongside `/changelog` and `/daily`), so the board's
  state history is browsable, not just ephemeral in chat.

**Where it runs.** The Discord side is one more classification in the existing
`github` container on the mini fleet (or a sibling unit); the wiki side is a
collector the dev-wiki render already fans in. A paid **GitHub Teams** plan adds
richer deployment/environment events and audit-log webhooks, sharpening the
deployment/release transitions — noted, not required for v1.

## Alternatives

- **Raw events only (status quo).** `github_discord.py` already posts PR-merged
  and releases. Rejected: it does not recognize *proposal-ready* or *board Status*
  moves, and phrases everything as raw activity, not workflow steps — the exact
  gap #163 calls out.
- **Push-only via a webhook receiver now.** The instant, single-fire path from
  `valkey-expansion.md`. Better latency, but needs a verified public ingress; this
  proposal ships poll-first on the existing scan and consumes the bus event once
  that lands. Not either/or.
- **A GitHub Actions workflow per repo that curls the webhook.** Works for
  merge/release, but can't see Project v2 Status moves (that's project-sync) and
  scatters notification logic across every repo. Rejected for centralization.
- **Wiki-only or Discord-only.** #163 asks for both surfaces; one envelope, two
  renderers keeps them consistent.

## Tasks

- [ ] Define the workflow-event envelope + `kind` taxonomy (shared shape above).
- [ ] Classifier: map `github_discord.py`'s fetched events (PR ready/merged,
      release, deployment) → envelopes; extend the existing scan, reuse `ChangeFeed`.
- [ ] Discord renderer: `discokit` embed per kind (palette + narrative), post via
      `poster.Poster` to `DISCORD_WEBHOOK_DEV`.
- [ ] project-sync hook: emit a `status_move` envelope when it changes an item's
      board Status (robot-geographical-society#161 / `.github` PR #17).
- [ ] dev-wiki collector: append envelopes to a workflow-events feed the dev-wiki
      render consumes (sibling of `/changelog`).
- [ ] Consume `fleet.workflow.event` from the bus once the webhook receiver
      (`valkey-expansion.md`) lands — poll-first, push-later.
- [ ] Tests: classification per kind + dedup gate (`ops/tests/`), no live post.
- [ ] Register the routing in `DISCORD.md`; note the GitHub Teams upgrade.

## Further Reading

- Master Idea: robot-geographical-society#163 ("Proposal X is ready to build" requirement)
- Board + columns: [Projects v2 #7](https://github.com/orgs/robogeosociety/projects/7)
- Prior art — GitHub→#dev feed + release/PR events: `ops/github_discord.py`
- Prior art — routing/post/dedup: `ops/discokit/` (`config.py`, `poster.py`, `notify.py`)
- Prior art — the webhook-receiver → bus event: `docs/proposals/valkey-expansion.md`, `docs/BUS.md`
- Prior art — dev-wiki render/serve: `obsidian-automations/dev-wiki/` (`serve.py`, `automations/dev_wiki.py`)
- project-sync (board Status source): robot-geographical-society#161, `robogeosociety/.github` PR #17
