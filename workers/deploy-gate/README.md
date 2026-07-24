# deploy-gate

The org's deploy-approval gate (issue #55; CICD-everything proposal
robogeosociety/robot-geographical-society#167 WS0): a Cloudflare Worker that turns
GitHub *custom deployment protection rules* into Discord Approve/Reject cards in `#dev`.

## Flow

1. A workflow job with `environment: production` starts in a repo whose environment
   lists the **rgs-deploy-gate** GitHub App as a deployment protection rule.
2. GitHub sends `deployment_protection_rule` to `POST /github` (HMAC-verified).
3. The Worker posts a card to `#dev` with Approve/Reject buttons, @-mentioning the
   operator (`allowed_mentions` scoped to that one user) so a pending deploy actually
   notifies instead of waiting silently. Only pending cards ping — approved,
   auto-approved, and `/notify` posts don't.
4. A button click (or `/deploy approve repo:<r> run_id:<id>`) — approvers only —
   answers GitHub's callback; the job proceeds or stays withheld.

**Auto-approve:** a run triggered by `workflow_dispatch` whose deployment creator is
in `AUTO_APPROVE_ACTORS` (wrangler `[vars]`, default `tommyroar`) skips the card —
the Worker answers the callback `approved` immediately and posts a one-line FYI to
`#dev`. Self-dispatching a deploy is already an expression of intent; merge/push
-triggered deploys keep the full card flow.

## Wiring a repo through the gate

```sh
gh api -X PUT /repos/robogeosociety/<repo>/environments/production >/dev/null
gh api -X POST /repos/robogeosociety/<repo>/environments/production/deployment_protection_rules \
  -F integration_id=4327530
```

then give the deploy job `environment: production`. Public repos only (env protection
is paywalled on private repos — their gating rides the mini runner instead).

## /notify — operator-attention lane

`POST /notify` posts a compact embed to `#dev` as the bot. Directive (2026-07-22):
anything pending Tommy — credential ceremonies, approvals outside the gate, blocked
chains — must arrive as a discobot post in `#dev`, not sit silently in an agent
session.

- Body: `{"title": "...", "body": "...", "level": "info|warn|error"}` (`level`
  optional, default `info`).
- Auth: HMAC-SHA256 of the raw body with **`NOTIFY_SECRET`** (dedicated secret,
  not `DG_REQUEST_SECRET`), sent as `x-request-signature: sha256=<hex>`.
- Key locations: repo secret `NOTIFY_SECRET` (synced to the Worker by CD); operator
  copy on the mini at `~/.config/deploy-gate/notify-key` (0600) so local agent
  sessions can sign requests.

Curl one-liner for agents (on the mini, or anywhere with a key copy):

```sh
BODY='{"title":"⏳ blocked: CF token ceremony","body":"tfvend apply needs a fresh dash login — session `xyz` parked","level":"warn"}' \
&& curl -sf https://deploy-gate.tommy-b-doerr.workers.dev/notify \
  -H "x-request-signature: sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$(cat ~/.config/deploy-gate/notify-key)" | awk '{print $NF}')" \
  -H 'content-type: application/json' -d "$BODY"
```

## Deploy

CD only: `.github/workflows/deploy-gate.yml` on merge to main (wrangler deploy,
secret sync, `/deploy` registration, app-webhook config). Secrets live in repo
secrets; nothing here is deployed by hand.
