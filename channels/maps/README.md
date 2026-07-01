# channels/maps — MapBot workspace

Source of truth for **MapBot**, the Claude Code channel agent that answers in Discord **#maps**
and **#transit** (one session, bound to both). It renders Mapbox maps, walksheds/transit imagery,
and live transit info, and is pinned to **sonnet** (mechanical tool-calling — see the babysitter's
`SESSION_MODEL`).

This directory deploys to the mini at `~/.claude/channels/discord-maps/workspace/` (the agent's
cwd). Secrets + per-channel state (`.env`, `access.json`, `chat_id`) stay on the mini, never here.

## Files

| File | Role |
| --- | --- |
| `CLAUDE.md` | the agent persona (loaded as cwd) — what MapBot is and which command to run per request |
| `mapbot.py` | the CLI MapBot drives: `map / zoom / pan / restyle / nearest / plot / station / walkshed / card / stations / styles / walkshot / board / arrivals / alerts / geo` |
| `walksheds.py` | local Seattle Link dataset (38 stations, ~26.5k POIs, tile grid) — gazetteer + curated POI search |
| `transit.py` | live OneBusAway: real-time `arrivals` (REST) + GTFS-Realtime service `alerts`; route names from local static GTFS |
| `geo.py` | transit-geo **semantic** search — bge-small embeddings (fastembed/ONNX) over the walksheds POIs |
| `render.mjs` | Playwright headless Mapbox GL screenshot |
| `webshot.mjs` | screenshot of the live walksheds.xyz app at a deeplink (cached per station) |
| `board.mjs` | render a departure-board HTML to PNG |
| `oblique.mjs` | oblique/3D terrain map render |
| `static-map.sh` | Mapbox Static Images API helper (no browser) |
| `cache_walkshots.py` | pre-render every station's walksheds.xyz screenshot into `cache/walkshots/` |
| `cache_geo.py` | build the bge geo index into `cache/geo/` |
| `walksheds-data/SNAPSHOT.md` | how the vendored walksheds data snapshot was taken + refresh command |

## External inputs (on the mini, not in this repo)

- **Mapbox token** `/Volumes/dev/maps/mapbox.key` (pk, URL-restricted to `tommyroar.github.io` → a
  `Referer` is sent automatically).
- **OBA key** `~/dev/transit_tracker/.local/service.yaml` (`oba_api_key:`).
- **Static GTFS** `/Volumes/dev/transit_tracker/data/gtfs/{1,40,95}/` (route names).
- **walksheds dataset** vendored to `walksheds-data/` (rsync from the Air — see `SNAPSHOT.md`).

## Rebuild (the git-ignored artifacts)

```sh
# deps
python3 -m pip install --target vendor fastembed gtfs-realtime-bindings   # numpy/onnxruntime come along
npm install                                                               # playwright
npx playwright install chromium

# data + caches
#   walksheds-data/: rsync from the Air per walksheds-data/SNAPSHOT.md
python3 cache_walkshots.py --filters coffee,restaurants,bars,parks        # ~14 min, 190 shots
python3 cache_geo.py                                                       # ~3 min, 26.5k embeddings
```

## Deploy to the mini

```sh
rsync -az --exclude-from=channels/maps/.gitignore channels/maps/ \
  tommydoerr@tommys-mac-mini.tail59a169.ts.net:.claude/channels/discord-maps/workspace/
# then restart the session:
ssh … 'nomad job dispatch -meta session=maps restart-maclaude'
```
