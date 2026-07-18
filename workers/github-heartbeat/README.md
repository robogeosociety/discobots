# github-heartbeat

The #dev development heartbeat as a Cloudflare Workers cron (issue #56;
CICD-everything proposal robogeosociety/robot-geographical-society#167 WS2) — the
first discobot lifted off the Mac mini. It replaces the `discobot-github`
container's 30-minute beat (`ops/github_discord.py`).

## What it posts (every 30 min, each exactly once)

- PRs opened / merged and issues opened / closed across recently-pushed org repos
- releases published
- red CI on default branches
- human-task board changes (issues labeled `human-task` opened / closed / commented)

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

The daily 08:00 PT check-in (`ops/dev_checkin.py`) is not lifted yet — it is the
planned second cron trigger on this Worker.

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
