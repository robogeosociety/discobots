# github-heartbeat

The #dev development heartbeat as a Cloudflare Workers cron (issue #56;
CICD-everything proposal robogeosociety/robot-geographical-society#167 WS2) — the
first discobot lifted off the Mac mini. It replaces both beats of the
`discobot-github` container — the 30-minute heartbeat (`ops/github_discord.py`)
and the daily dev check-in (`ops/dev_checkin.py`): two cron triggers, one
Worker, dispatched on `controller.cron`.

## The heartbeat (every 30 min, each exactly once)

- PRs opened / merged and issues opened / closed across recently-pushed org repos
- releases published
- red CI on default branches
- human-task board changes (issues labeled `human-task` opened / closed / commented)

## The check-in (daily 15:03 UTC ≈ 08:00 PDT; lands 07:03 PT under winter PST — Workers crons are UTC-only)

One ☕ status embed: PRs merged since the last check-in, CI health chips for
repos pushed within 7 days, the open human-task queue, and the next scheduled
repo-sync (Mondays 07:17 UTC). The merged-PR list comes from a pushed-since
repo walk (a merge always bumps `pushed_at`) instead of the container's
org-wide search query — the search API rides on user tokens.

## How it differs from the container bot

| | container (`ops/github_discord.py`) | this Worker |
|---|---|---|
| GitHub auth | tommyroar's `gh` token | rgs-deploy-gate GitHub App (JWT → org installation token) |
| Activity source | user org-dashboard events feed | per-repo REST scans (pulls / issues / releases / actions runs) — the events feed is user-token-only |
| Posting | `DISCORD_WEBHOOK_DEV` webhook | bot-token REST to `#dev` (the deploy-gate pattern) |
| State | JSON file in a named volume | KV (`STATE` binding, one `state` key — same ChangeFeed/StateFile shapes) |

A scan category the app lacks permission for logs a warning and skips; it heals
on the first run after the permission lands. Required app permissions beyond
deploy-gate's (`actions:read`, `metadata:read`): **`pull_requests:read`,
`issues:read`, `contents:read`** (releases ride on contents).

## Deploy

CD only: `.github/workflows/github-heartbeat.yml` on merge to main (deploy-gated
`environment: production`). It resolves/creates the `github-heartbeat-state` KV
namespace via the Cloudflare API, substitutes the id into `wrangler.toml`, runs
`wrangler deploy`, and syncs the two Worker secrets from repo secrets. Nothing is
deployed by hand.

## Local test

```sh
cd workers/github-heartbeat
npx wrangler@4 dev --test-scheduled     # then: curl 'http://localhost:8787/__scheduled'
```
