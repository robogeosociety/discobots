# skills-feed

The #skills announcer as a Cloudflare Workers cron (issue #56; CICD-everything
proposal robogeosociety/robot-geographical-society#167 WS2, lift #4) — the
Workers port of the `skills` container (`ops/skills_discord.py`), and the last
lift that isn't WS5-blocked.

Two beats, one Worker (dispatched on `controller.cron`):

- **every 3 h** — announce each 🆕 skill the fleet has gained, exactly once
  (version-independent keys, so a plugin version bump never re-announces); the
  very first run seeds silently and posts a single 📚 intro.
- **daily 16:33 UTC** (≈ 09:30 PT; 08:33 PT under winter PST — UTC-only crons)
  — one 💡 spotlight on an existing, not-recently-featured skill, via the
  container's deterministic counter rotation with a cooldown of 8.

## The inventory-source rework

The container read the mini's `~/.claude/{skills,plugins}` through ro mounts — a
Worker can't reach a filesystem. The split:

- **Publisher (mini-side):** the `skills-inventory` job in
  **robogeosociety/supervisor** scans those trees (same discovery rules +
  front-matter parser as the container) and, on change, PUTs
  `{generated_at, skills: [{key, name, description, source, since}]}` to the
  `inventory` key of this Worker's KV namespace via the Cloudflare API. It rides
  the supervisor's existing pull-based autodeploy (~2 min from merge) — no new
  deploy mechanism, no new daemon.
- **This Worker:** reads `inventory`, owns `state` (known skills, spotlight
  rotation, resolved channel id). Posts to `#skills` via bot-token REST.

Why KV-push instead of committing the inventory to a repo: the inventory is a
derived artifact (fleet convention: regenerate, never commit), a commit lane
would spam a code repo + its CI/deploy-gate on every skill change, freshness is
minutes rather than merge latency, and `cloudflare-tfvend` exists exactly to
mint the least-privilege KV-write token the publisher needs.

A missing/empty inventory logs and skips (the Worker never guesses); an
inventory older than 48 h logs a wedged-publisher warning but still serves.

## Deploy

CD only: `.github/workflows/skills-feed.yml` on merge to main (deploy-gated
`environment: production`): resolves/creates the `skills-feed-state` KV
namespace, substitutes the id, `wrangler deploy`, syncs `DISCORD_BOT_TOKEN`.

Wiring (once): vend a Workers-KV-Edit Cloudflare token via `cloudflare-tfvend`
for the mini publisher and place it per the supervisor job's docs.

## Local test

```sh
cd workers/skills-feed
npx wrangler@4 dev --test-scheduled
curl 'http://localhost:8787/__scheduled?cron=0+*/3+*+*+*'    # the new-skill beat
curl 'http://localhost:8787/__scheduled?cron=33+16+*+*+*'    # the spotlight
```
