# walksheds-data — vendored snapshot

Point-in-time copy of the Seattle Link walksheds dataset, used offline by `walksheds.py`.

- **Source:** `/Users/tommy/dev/walksheds/public/` on the MacBook Air (deployed at walksheds.xyz).
- **Snapshot taken:** 2026-06-29.
- **Contents:** `all-stations.geojson` (38 stations), `station-exits.geojson`,
  `line1/2-alignment.geojson`, `pois/tag-categories.json`, `pois/tiles/` (~1074 tiles, 26,510 POIs)
  + `pois/tiles/index.json` (tile_deg 0.01, station→tiles map).

## Refresh (run from the Air, where the source lives)

```sh
cd /Users/tommy/dev/walksheds/public
H=tommydoerr@tommys-mac-mini.tail59a169.ts.net
D=.claude/channels/discord-maps/workspace/walksheds-data
rsync -az all-stations.geojson station-exits.geojson line1-alignment.geojson line2-alignment.geojson "$H:$D/"
rsync -az --delete pois/ "$H:$D/pois/"
```

The data is static GeoJSON; no rebuild step. After refreshing, update the date above.
