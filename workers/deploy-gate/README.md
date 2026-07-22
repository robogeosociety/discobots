# deploy-gate

The org's deploy-approval gate (issue #55; CICD-everything proposal
robogeosociety/robot-geographical-society#167 WS0): a Cloudflare Worker that turns
GitHub *custom deployment protection rules* into Discord Approve/Reject cards in `#dev`.

## Flow

1. A workflow job with `environment: production` starts in a repo whose environment
   lists the **rgs-deploy-gate** GitHub App as a deployment protection rule.
2. GitHub sends `deployment_protection_rule` to `POST /github` (HMAC-verified).
3. The Worker posts a card to `#dev` with Approve/Reject buttons.
4. A button click (or `/deploy approve repo:<r> run_id:<id>`) — approvers only —
   answers GitHub's callback; the job proceeds or stays withheld.

## Wiring a repo through the gate

```sh
gh api -X PUT /repos/robogeosociety/<repo>/environments/production >/dev/null
gh api -X POST /repos/robogeosociety/<repo>/environments/production/deployment_protection_rules \
  -F integration_id=4327530
```

then give the deploy job `environment: production`. Public repos only (env protection
is paywalled on private repos — their gating rides the mini runner instead).

## Dead-letter path

Card posting can fail independently of GitHub — a rotated-out bot token or channel
perms (the 2026-07-21 incident: runs sat pending 40+ min with no card and no signal).
Three layers keep that from being silent:

1. **5xx on failure** — `/github` and `/request` post the card *before* answering;
   a failed post returns 500/502 so the delivery is marked failed (and the
   `/request` caller's `curl -sf` step goes red) instead of a lying 200.
2. **Fallback alert** — every failure pings `DISCORD_FALLBACK_WEBHOOK`, a plain
   channel webhook URL that works without the bot token.
3. **Cron self-heal** (`*/10`) — GitHub does **not** redeliver failed deliveries on
   its own, so the tick validates the bot token (nags the fallback webhook while
   it's invalid) and, once healthy, redelivers failed
   `deployment_protection_rule` deliveries (≤ 6 h old) via the App API — the
   missing cards then post with no operator action.

`GET /health` does a live token check (`200` valid / `503` stale) for external probes.

## Deploy

CD only: `.github/workflows/deploy-gate.yml` on merge to main (wrangler deploy,
secret sync, `/deploy` registration, app-webhook config). Secrets live in repo
secrets; nothing here is deployed by hand.
