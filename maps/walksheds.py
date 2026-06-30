"""Vendored Seattle Link walkshed dataset for MapBot.

A local snapshot of github.com/tommy walksheds (deployed at walksheds.xyz): 38 Link stations,
~26.5k curated POIs on a 0.01-degree tile grid, and a station->tiles index. Snapshot lives in
walksheds-data/ next to this file; refresh it with sync-walksheds.sh.

Everything here is offline (no network) — geocoding/search for the Seattle Link service area is
answered from this data instead of OSM/Mapbox, which is faster and curated (rich tags, facets).
"""
import functools
import json
import math
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "walksheds-data")
TILES = os.path.join(DATA, "pois", "tiles")
TILE_DEG = 0.01

# Sound Transit Link line colors (hex without '#'); shared segment uses the 1 Line green.
LINE_COLORS = {"1": "38B030", "2": "00A0E0", "1,2": "38B030"}
# Link service-area bbox (lon,lat): Lynnwood -> Angle Lake / Redmond. Used to decide when to
# answer from this dataset vs. fall back to OSM.
SEA_BBOX = (-122.46, 47.29, -122.04, 47.84)


def haversine(lon1, lat1, lon2, lat2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def in_seattle(lon, lat):
    x0, y0, x1, y1 = SEA_BBOX
    return x0 <= lon <= x1 and y0 <= lat <= y1


@functools.lru_cache(1)
def _stations():
    fc = json.load(open(os.path.join(DATA, "all-stations.geojson")))
    out = []
    for f in fc["features"]:
        p = f["properties"]; lon, lat = f["geometry"]["coordinates"]
        out.append({
            "key": "%s-%s" % (p["lines"], p["stopCode"]), "name": p["name"],
            "lon": lon, "lat": lat, "lines": p["lines"], "stopCode": p["stopCode"],
            "color": LINE_COLORS.get(p["lines"], "38B030"),
        })
    return out


def stations():
    return _stations()


# Common shorthands -> a substring of the official station name.
STATION_ALIASES = {
    "uw": "university of washington", "u district": "u district", "udistrict": "u district",
    "airport": "seatac/airport", "seatac": "seatac/airport", "sea-tac": "seatac/airport",
    "downtown": "westlake", "cap hill": "capitol hill", "caphill": "capitol hill",
    "clink": "stadium", "the link": "stadium",
}


def resolve_station(q):
    """Fuzzy-match a query to a Link station, or None."""
    s = q.strip().lower()
    s = re.sub(r"\bstations?\b", "", s).strip()
    s = STATION_ALIASES.get(s, s)
    if not s:
        return None
    sts = _stations()
    cands = [st for st in sts if s in st["name"].lower()]
    if not cands:
        toks = set(s.split())
        cands = [st for st in sts if toks and toks <= set(st["name"].lower().split())]
    if not cands:
        return None
    return sorted(cands, key=lambda st: len(st["name"]))[0]


def nearest_station(lon, lat):
    return min(_stations(), key=lambda st: haversine(lon, lat, st["lon"], st["lat"]))


@functools.lru_cache(1)
def _index():
    return json.load(open(os.path.join(TILES, "index.json")))


@functools.lru_cache(maxsize=8192)
def _tile(key):
    p = os.path.join(TILES, "%s.geojson" % key)
    if not os.path.exists(p):
        return ()
    return tuple(json.load(open(p))["features"])


def tiles_around(lon, lat, cells=2):
    c0 = math.floor(lon / TILE_DEG); r0 = math.floor(lat / TILE_DEG)
    return ["%d_%d" % (c, r) for c in range(c0 - cells, c0 + cells + 1)
            for r in range(r0 - cells, r0 + cells + 1)]


def station_tiles(key):
    return _index().get("station_tiles", {}).get(key, [])


# Map everyday words to the tag/category vocabulary actually present in the data.
QUERY_SYNONYMS = {
    "cafe": "coffee", "coffeeshop": "coffee", "food": "restaurant", "eat": "restaurant",
    "bars": "bar", "pub": "bar", "drinks": "bar", "parks": "park", "veggie": "vegetarian",
    "wheelchair": "wheelchair-accessible", "accessible": "wheelchair-accessible",
    "kid": "child-friendly", "kids": "child-friendly", "child": "child-friendly",
    "family": "child-friendly", "weed": "cannabis", "dispensary": "cannabis",
    "grocery": "supermarket", "groceries": "supermarket", "museums": "museum",
}
_STOP = {"the", "a", "an", "near", "nearest", "find", "show", "me", "to", "of", "around",
         "in", "at", "by", "some", "any", "plot", "map", "and"}


def normalize_terms(query):
    words = re.findall(r"[a-z'+-]+", query.lower())
    return [QUERY_SYNONYMS.get(w, w) for w in words if w not in _STOP]


def _match(feat, terms):
    """A POI matches if EVERY term is its category or one of its tags (light plural handling)."""
    p = feat["properties"]
    tags = set(t.lower() for t in (p.get("tags") or []))
    cat = (p.get("category") or "").lower()
    for t in terms:
        if t == cat or t in tags or t.rstrip("s") in tags or (t + "s") in tags:
            continue
        return False
    return True


def _collect(tile_keys, terms, ref):
    out, seen = [], set()
    for k in tile_keys:
        for f in _tile(k):
            fid = f["properties"].get("id")
            if fid in seen:
                continue
            if _match(f, terms):
                seen.add(fid)
                flon, flat = f["geometry"]["coordinates"]
                out.append((haversine(ref[0], ref[1], flon, flat), f))
    out.sort(key=lambda x: x[0])
    return out


def search_near(lon, lat, query, limit=12, cells=2):
    terms = normalize_terms(query)
    return terms, _collect(tiles_around(lon, lat, cells), terms, (lon, lat))[:limit]


def search_station(station, query, limit=20):
    terms = normalize_terms(query)
    return terms, _collect(station_tiles(station["key"]), terms, (station["lon"], station["lat"]))[:limit]
