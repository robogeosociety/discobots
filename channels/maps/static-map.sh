#!/usr/bin/env bash
# Render a Mapbox static map centered on a POI -> out/map.png (Static Images API, no browser).
# Usage:
#   ./static-map.sh "<place name | address | lat,lon>" [zoom] [style]
# Examples:
#   ./static-map.sh "Space Needle, Seattle" 15
#   ./static-map.sh "47.6205,-122.3493" 14
#   ./static-map.sh "Pike Place Market" 16 dark-v11
set -euo pipefail

KEY_FILE="/Volumes/dev/maps/mapbox.key"
[ -r "$KEY_FILE" ] || { echo "mapbox key not readable at $KEY_FILE" >&2; exit 1; }
TOKEN="$(tr -d ' \n\r' < "$KEY_FILE")"
# The pk token is URL-restricted to the maps site origin; send it as the Referer so
# server-side requests are accepted (override with MAPBOX_REFERER if the allowlist changes).
REFERER="${MAPBOX_REFERER:-https://tommyroar.github.io/}"

Q="${1:?usage: static-map.sh <place|lat,lon> [zoom] [style]}"
ZOOM="${2:-14}"
STYLE="${3:-streets-v12}"
SIZE="${SIZE:-800x600}"

DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/out"; mkdir -p "$OUT"

# Resolve to lon,lat. Accept "lat,lon" directly (human order), else geocode the name.
if printf '%s' "$Q" | grep -qE '^-?[0-9]+(\.[0-9]+)?,-?[0-9]+(\.[0-9]+)?$'; then
  LAT="${Q%%,*}"; LON="${Q##*,}"
else
  ENC="$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$Q")"
  read -r LON LAT <<<"$(curl -fsS -e "$REFERER" --get \
      "https://api.mapbox.com/geocoding/v5/mapbox.places/${ENC}.json" \
      -d limit=1 -d access_token="$TOKEN" \
    | python3 -c 'import sys,json; f=json.load(sys.stdin).get("features") or []; print(*f[0]["center"]) if f else sys.exit("no geocode match")')"
fi

URL="https://api.mapbox.com/styles/v1/mapbox/${STYLE}/static/pin-l+ff2200(${LON},${LAT})/${LON},${LAT},${ZOOM},0/${SIZE}@2x?access_token=${TOKEN}"
curl -fsS -e "$REFERER" -o "$OUT/map.png" "$URL" || { echo "static image request failed" >&2; exit 1; }

# Guard against a JSON error body saved as map.png
if file "$OUT/map.png" | grep -qi 'PNG image'; then
  echo "$OUT/map.png  ($Q -> $LAT,$LON z$ZOOM)"
else
  echo "mapbox returned a non-image (likely an error):" >&2
  head -c 400 "$OUT/map.png" >&2; echo >&2
  exit 1
fi
