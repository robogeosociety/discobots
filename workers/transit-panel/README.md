# transit-panel

The #transit live status panel as a Cloudflare Workers cron (issue #56;
CICD-everything proposal robogeosociety/robot-geographical-society#167 WS2, lift
#3) — the Workers port of the `transit-panel` container
(`ops/transit_dashboard.py`).

Every minute: fetch the OneBusAway GTFS-Realtime alerts feeds (King County
Metro + Sound Transit), reduce to the watched-line chip row (🟢 ok / 🟡🟠🔴 by
worst active effect, down-first) with the actionable alert headers below, and
reconcile ONE Discord message that edits itself in place — the
`discokit.Dashboard` contract: content-hash diff, freshness stamp (`updated
<t:…:R>`) bumped only on real change, unreachable feed shows last-known state,
a deleted panel re-posts itself.

## How it differs from the container daemon

| | container (`ops/transit_dashboard.py`) | this Worker |
|---|---|---|
| GTFS-RT decode | `gtfs-realtime-bindings` protobuf | hand-rolled ~60-line reader for the Alert subset (no deps, single-file Worker convention) |
| Posting | `DISCORD_WEBHOOK_TRANSIT` webhook | bot-token REST to `#transit` |
| State | JSON file in a named volume | KV (`STATE` binding, one `state` key) — written only when content changes, so an all-quiet minute is 1 KV read + 2 OBA fetches, no Discord/KV writes |

**Not lifted:** the discrete alert notifier (`ops/transit_discord.py`
FIRING/Cleared embeds). The panel exists to collapse that churn (~62 msgs/day);
its planned cutover retires the notifier rather than porting it.

## Deploy

CD only: `.github/workflows/transit-panel.yml` on merge to main (deploy-gated
`environment: production`). It resolves/creates the `transit-panel-state` KV
namespace via the Cloudflare API, substitutes the id into `wrangler.toml`, runs
`wrangler deploy`, and syncs `DISCORD_BOT_TOKEN` + `OBA_API_KEY` from repo
secrets. The `OBA_API_KEY` repo secret mirrors the mini's
`~/dev/transit_tracker/.local/service.yaml` `oba_api_key`.

## Local test

```sh
cd workers/transit-panel
npx wrangler@4 dev --test-scheduled     # then: curl 'http://localhost:8787/__scheduled'
```
