#!/usr/bin/env python3
"""MapBot map tool — Mapbox Static Images with conversational options + Seattle Link walksheds.

Every render writes out/map.png (or .jpg for satellite) and remembers the view in out/last.json,
so relative follow-ups (zoom, restyle, pan) work without re-stating the place.

General:
  map <place|lat,lon> [--zoom N] [--style S] [--marker lat,lon[:color] ...]
  zoom <in|out> [--step N]            pan <place|lat,lon>            restyle <style>
  nearest "<query>" [--around <place|station|lat,lon>]
  styles

Seattle Link walksheds (vendored dataset, see walksheds.py):
  stations                            list the 38 Link stations
  station "<name>" [--walk MIN]       map a station (line-colored), optionally with its walkshed
  walkshed "<station|place>" [MIN]    draw the MIN-minute walking isochrone (default 15)
  plot "<query>" --at "<station|place>" [--limit N]   plot curated POIs (vegan, coffee, ...) there

Geocoding/POI search use OpenStreetMap (Nominatim + Overpass) globally, and the local walksheds
snapshot inside the Seattle Link service area. Token (/Volumes/dev/maps/mapbox.key) is only used
to render tiles + fetch isochrones; it is URL-restricted, so a Referer is sent automatically.
"""
import argparse, json, math, os, re, shutil, subprocess, sys, time, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import walksheds as ws

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out"); os.makedirs(OUT, exist_ok=True)
STATE = os.path.join(OUT, "last.json")
TOKEN = open("/Volumes/dev/maps/mapbox.key").read().strip()
REFERER = os.environ.get("MAPBOX_REFERER", "https://tommyroar.github.io/")
SIZE = os.environ.get("SIZE", "800x600")
UA = "MapBot/1 (tommy.b.doerr@gmail.com; https://tommyroar.github.io)"

STYLES = {
    "streets-v12": "default street map", "outdoors-v12": "terrain & trails",
    "light-v11": "minimal light", "dark-v11": "minimal dark",
    "satellite-v9": "satellite imagery", "satellite-streets-v12": "satellite + labels",
    "navigation-day-v1": "navigation (day)", "navigation-night-v1": "navigation (night)",
}
DEFAULT_STYLE = "streets-v12"


def _get(url, tries=3):
    req = urllib.request.Request(url, headers={"Referer": REFERER, "User-Agent": UA})
    last = None
    for i in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=30)
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(1.5 * (i + 1))
    raise last


def haversine(lon1, lat1, lon2, lat2):
    return ws.haversine(lon1, lat1, lon2, lat2)


def short(name):
    return ", ".join((name or "").split(", ")[:3])


# ---- geocoding / POI search (OSM) ------------------------------------------------
def geocode(query, proximity=None):
    params = {"q": query, "format": "jsonv2", "limit": "1"}
    if proximity:
        lon, lat = proximity
        params["viewbox"] = "%f,%f,%f,%f" % (lon - 0.6, lat + 0.6, lon + 0.6, lat - 0.6)
        params["bounded"] = "1"
    arr = json.load(_get("https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(params)))
    if not arr:
        return None
    r = arr[0]
    return {"lon": float(r["lon"]), "lat": float(r["lat"]), "name": r.get("display_name")}


def resolve(s):
    if re.match(r"^-?\d+(\.\d+)?,\s*-?\d+(\.\d+)?$", s.strip()):
        lat, lon = [float(x) for x in s.split(",")]
        return {"lon": lon, "lat": lat, "name": "%.5f,%.5f" % (lat, lon)}
    g = geocode(s)
    if not g:
        sys.exit("no geocode match for %r" % s)
    return g


def resolve_anchor(s):
    """A Link station name (local gazetteer) else a geocoded place."""
    st = ws.resolve_station(s)
    if st:
        return {"lon": st["lon"], "lat": st["lat"], "name": st["name"]}
    return resolve(s)


def resolve_seattle(s):
    """Like resolve_anchor but biases place geocoding to the Seattle Link area, so 'Ballard'
    resolves to the neighborhood, not Ballard County, Kentucky."""
    st = ws.resolve_station(s)
    if st:
        return {"lon": st["lon"], "lat": st["lat"], "name": st["name"]}
    cx = (ws.SEA_BBOX[0] + ws.SEA_BBOX[2]) / 2
    cy = (ws.SEA_BBOX[1] + ws.SEA_BBOX[3]) / 2
    g = geocode(s, proximity=(cx, cy))
    if not g:
        sys.exit("no match for %r near Seattle" % s)
    return g


CATEGORIES = {
    "gas station": '["amenity"="fuel"]', "gas": '["amenity"="fuel"]', "fuel": '["amenity"="fuel"]',
    "petrol": '["amenity"="fuel"]', "charging": '["amenity"="charging_station"]',
    "ev charger": '["amenity"="charging_station"]', "atm": '["amenity"="atm"]',
    "bank": '["amenity"="bank"]', "parking": '["amenity"="parking"]', "hospital": '["amenity"="hospital"]',
    "pharmacy": '["amenity"="pharmacy"]', "drugstore": '["amenity"="pharmacy"]',
    "supermarket": '["shop"="supermarket"]', "grocery": '["shop"="supermarket"]',
    "post office": '["amenity"="post_office"]', "police": '["amenity"="police"]',
    "library": '["amenity"="library"]', "toilet": '["amenity"="toilets"]',
}


def category_filter(query):
    q = query.lower().strip()
    for word, filt in CATEGORIES.items():
        if word in q:
            return filt
    return None


def nearest_poi_osm(query, lon, lat):
    filt = category_filter(query)
    if filt is None:
        g = geocode(query, proximity=(lon, lat))
        if not g:
            return None
        return (haversine(lon, lat, g["lon"], g["lat"]), g["name"], g["lon"], g["lat"])
    for radius in (3000, 10000, 25000):
        oq = "[out:json][timeout:25];(node(around:%d,%f,%f)%s;way(around:%d,%f,%f)%s;);out center 60;" % (
            radius, lat, lon, filt, radius, lat, lon, filt)
        data = json.load(_get("https://overpass-api.de/api/interpreter?data=" + urllib.parse.quote(oq), tries=2))
        best = None
        for e in data.get("elements", []):
            if e.get("type") == "node":
                elat, elon = e["lat"], e["lon"]
            elif "center" in e:
                elat, elon = e["center"]["lat"], e["center"]["lon"]
            else:
                continue
            nm = (e.get("tags") or {}).get("name") or query
            d = haversine(lon, lat, elon, elat)
            if best is None or d < best[0]:
                best = (d, nm, elon, elat)
        if best:
            return best
    return None


# ---- isochrone (walkshed) ---------------------------------------------------------
def encode_polyline(coords):
    """Google polyline (precision 1e5) of [(lat, lon), ...]."""
    res, last_lat, last_lon = [], 0, 0
    for lat, lon in coords:
        ilat, ilon = int(round(lat * 1e5)), int(round(lon * 1e5))
        for d in (ilat - last_lat, ilon - last_lon):
            d = (d << 1) ^ (d >> 31)
            while d >= 0x20:
                res.append(chr((0x20 | (d & 0x1f)) + 63)); d >>= 5
            res.append(chr(d + 63))
        last_lat, last_lon = ilat, ilon
    return "".join(res)


def isochrone_polyline(lon, lat, minutes):
    url = ("https://api.mapbox.com/isochrone/v1/mapbox/walking/%f,%f"
           "?contours_minutes=%d&polygons=true&denoise=1&access_token=%s" % (lon, lat, minutes, TOKEN))
    try:
        data = json.load(_get(url))
    except Exception:
        return None
    feats = data.get("features") or []
    if not feats:
        return None
    ring = feats[0]["geometry"]["coordinates"][0]            # outer ring [lon,lat]
    if len(ring) > 45:                                       # decimate to keep the overlay URL short
        step = len(ring) / 45.0
        ring = [ring[int(i * step)] for i in range(45)] + [ring[-1]]
    return encode_polyline([(la, lo) for lo, la in ring])


def path_overlay(encoded, color="38B030", fill_opacity=0.14):
    return "path-3+%s-0.95+%s-%.2f(%s)" % (color, color, fill_opacity, urllib.parse.quote(encoded, safe=""))


def marker_overlay(m):
    icon = ("-" + m["icon"]) if m.get("icon") else ""    # optional Maki glyph, e.g. pin-s-cafe
    return "pin-%s%s+%s(%s,%s)" % (m.get("size", "l"), icon, m["color"], m["lon"], m["lat"])


# Spotlight categories for the station "card": (search term, Maki icon, dot color).
CARD_SPOTLIGHTS = [
    ("restaurant", "restaurant", "e74c3c"),   # red
    ("coffee", "cafe", "b5651d"),             # brown
    ("bar", "bar", "8e44ad"),                 # purple
    ("park", "park", "159957"),               # green
]


# ---- render + state ---------------------------------------------------------------
def static_url(overlays, viewport, style):
    seg = (",".join(overlays) + "/") if overlays else ""
    return "https://api.mapbox.com/styles/v1/mapbox/%s/static/%s%s/%s@2x?access_token=%s" % (
        style, seg, viewport, SIZE, TOKEN)


def render(markers, viewport, style, path=None):
    overlays = ([path] if path else []) + [marker_overlay(m) for m in markers]   # path under pins
    data = _get(static_url(overlays, viewport, style)).read()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ext = "png"
    elif data[:3] == b"\xff\xd8\xff":
        ext = "jpg"
    else:
        sys.exit("mapbox returned a non-image: " + data[:200].decode("utf8", "replace"))
    for e in ("png", "jpg"):
        stale = os.path.join(OUT, "map.%s" % e)
        if e != ext and os.path.exists(stale):
            os.remove(stale)
    p = os.path.join(OUT, "map.%s" % ext)
    open(p, "wb").write(data)
    return p


def save_state(center, zoom, style, markers, label, path=None):
    json.dump({"center": center, "zoom": zoom, "style": style, "markers": markers,
               "label": label, "path": path}, open(STATE, "w"))


def load_state():
    if not os.path.exists(STATE):
        sys.exit("no previous map yet — render one first (map <place>)")
    return json.load(open(STATE))


def clampz(z):
    return max(0, min(22, z))


def viewport(center, zoom, path):
    return "auto" if path else "%s,%s,%s,0" % (center["lon"], center["lat"], zoom)


# ---- subcommands ------------------------------------------------------------------
def do_map(a):
    st = ws.resolve_station(a.place) if "station" in a.place.lower() else None
    if st:
        p = {"lon": st["lon"], "lat": st["lat"], "name": st["name"]}; pin = st["color"]
    else:
        p = resolve(a.place); pin = "ff2200"
    zoom = a.zoom if a.zoom is not None else 14
    style = a.style or DEFAULT_STYLE
    markers = [{"lon": p["lon"], "lat": p["lat"], "color": pin, "size": "l"}]
    for mk in a.marker or []:
        coord, _, color = mk.partition(":")
        lat, lon = [float(x) for x in coord.split(",")]
        markers.append({"lon": lon, "lat": lat, "color": color or "2266ff", "size": "s"})
    out = render(markers, "%s,%s,%s,0" % (p["lon"], p["lat"], zoom), style)
    save_state({"lon": p["lon"], "lat": p["lat"]}, zoom, style, markers, short(p["name"]))
    print("%s  (%s -> %.5f,%.5f  z%s  %s)" % (out, short(p["name"]), p["lat"], p["lon"], zoom, style))


def do_zoom(a):
    st = load_state(); step = a.step or 2
    zoom = clampz(st["zoom"] + (step if a.direction == "in" else -step))
    out = render(st["markers"], viewport(st["center"], zoom, st.get("path")), st["style"], st.get("path"))
    save_state(st["center"], zoom, st["style"], st["markers"], st["label"], st.get("path"))
    print("%s  (%s  z%s  %s)" % (out, st["label"], zoom, st["style"]))


def do_pan(a):
    st = load_state(); p = resolve(a.place)
    markers = [{"lon": p["lon"], "lat": p["lat"], "color": "ff2200", "size": "l"}]
    out = render(markers, "%s,%s,%s,0" % (p["lon"], p["lat"], st["zoom"]), st["style"])
    save_state({"lon": p["lon"], "lat": p["lat"]}, st["zoom"], st["style"], markers, short(p["name"]))
    print("%s  (%s  z%s  %s)" % (out, short(p["name"]), st["zoom"], st["style"]))


def do_restyle(a):
    if a.style not in STYLES:
        sys.exit("unknown style %r — run: mapbot.py styles" % a.style)
    st = load_state()
    out = render(st["markers"], viewport(st["center"], st["zoom"], st.get("path")), a.style, st.get("path"))
    save_state(st["center"], st["zoom"], a.style, st["markers"], st["label"], st.get("path"))
    print("%s  (%s  z%s  %s)" % (out, st["label"], st["zoom"], a.style))


def do_nearest(a):
    if a.around:
        anchor = resolve_anchor(a.around)
    else:
        st = load_state(); anchor = {"lon": st["center"]["lon"], "lat": st["center"]["lat"], "name": st["label"]}
    style = DEFAULT_STYLE
    try:
        style = load_state()["style"]
    except SystemExit:
        pass
    # Seattle Link area -> curated walksheds dataset first.
    if ws.in_seattle(anchor["lon"], anchor["lat"]):
        terms, res = ws.search_near(anchor["lon"], anchor["lat"], a.query, limit=1, cells=3)
        if res:
            d, f = res[0]; lon, lat = f["geometry"]["coordinates"]; name = f["properties"].get("name", a.query)
            markers = [{"lon": anchor["lon"], "lat": anchor["lat"], "color": "2266ff", "size": "s"},
                       {"lon": lon, "lat": lat, "color": "ff2200", "size": "l"}]
            out = render(markers, "auto", style)
            save_state({"lon": lon, "lat": lat}, 15, style, markers, name)
            print("%s  (nearest %r [walksheds]: %s — %.2f km from %s)" % (out, a.query, name, d, short(anchor["name"])))
            return
    res = nearest_poi_osm(a.query, anchor["lon"], anchor["lat"])
    if not res:
        sys.exit("couldn't find %r near %s — try a category word or the exact name" % (a.query, short(anchor["name"])))
    d, name, lon, lat = res
    markers = [{"lon": anchor["lon"], "lat": anchor["lat"], "color": "2266ff", "size": "s"},
               {"lon": lon, "lat": lat, "color": "ff2200", "size": "l"}]
    out = render(markers, "auto", style)
    save_state({"lon": lon, "lat": lat}, 14, style, markers, short(name))
    print("%s  (nearest %r: %s — %.2f km from %s)" % (out, a.query, short(name), d, short(anchor["name"])))


def do_plot(a):
    st = ws.resolve_station(a.at)
    if st:
        terms, res = ws.search_station(st, a.query, limit=a.limit or 15)
        anchor = {"lon": st["lon"], "lat": st["lat"], "name": st["name"], "color": st["color"]}
    else:
        anchor = resolve_seattle(a.at)
        if not ws.in_seattle(anchor["lon"], anchor["lat"]):
            sys.exit("plot uses the Seattle Link walksheds dataset — give a Link station or a Seattle place via --at")
        anchor["color"] = "2266ff"
        terms, res = ws.search_near(anchor["lon"], anchor["lat"], a.query, limit=a.limit or 15, cells=3)
    if not res:
        sys.exit("no %r found near %s (tags: %s)" % (a.query, short(anchor["name"]), ",".join(terms)))
    markers = [{"lon": anchor["lon"], "lat": anchor["lat"], "color": anchor["color"], "size": "l"}]
    for _, f in res:
        flon, flat = f["geometry"]["coordinates"]
        markers.append({"lon": flon, "lat": flat, "color": "ff2200", "size": "s"})
    out = render(markers, "auto", load_state_style())
    save_state({"lon": anchor["lon"], "lat": anchor["lat"]}, 14, load_state_style(), markers, short(anchor["name"]))
    names = ", ".join(f["properties"].get("name", "?") for _, f in res[:5])
    print("%s  (%d %r near %s: %s%s)" % (out, len(res), a.query, short(anchor["name"]), names,
                                         " ..." if len(res) > 5 else ""))


def do_station(a):
    st = ws.resolve_station(a.name)
    if not st:
        sys.exit("no Link station matches %r — run: mapbot.py stations" % a.name)
    path = None
    if a.walk:
        enc = isochrone_polyline(st["lon"], st["lat"], a.walk)
        path = path_overlay(enc, st["color"]) if enc else None
    markers = [{"lon": st["lon"], "lat": st["lat"], "color": st["color"], "size": "l"}]
    vp = "auto" if path else "%s,%s,15,0" % (st["lon"], st["lat"])
    out = render(markers, vp, DEFAULT_STYLE, path)
    save_state({"lon": st["lon"], "lat": st["lat"]}, 15, DEFAULT_STYLE, markers, st["name"], path)
    extra = "  +%d-min walkshed" % a.walk if path else ""
    print("%s  (%s  Line %s%s)" % (out, st["name"], st["lines"], extra))


def do_walkshed(a):
    st = ws.resolve_station(a.place)
    if st:
        pt = {"lon": st["lon"], "lat": st["lat"], "name": st["name"], "color": st["color"]}
    else:
        g = resolve(a.place); pt = {"lon": g["lon"], "lat": g["lat"], "name": short(g["name"]), "color": "d33"}
    enc = isochrone_polyline(pt["lon"], pt["lat"], a.minutes)
    if not enc:
        sys.exit("couldn't fetch a walking isochrone for %s" % short(pt["name"]))
    path = path_overlay(enc, pt["color"])
    markers = [{"lon": pt["lon"], "lat": pt["lat"], "color": pt["color"], "size": "l"}]
    out = render(markers, "auto", DEFAULT_STYLE, path)
    save_state({"lon": pt["lon"], "lat": pt["lat"]}, 14, DEFAULT_STYLE, markers, pt["name"], path)
    print("%s  (%d-min walk from %s)" % (out, a.minutes, pt["name"]))


def do_card(a):
    st = ws.resolve_station(a.station)
    if not st:
        sys.exit("no Link station matches %r — run: mapbot.py stations" % a.station)
    minutes = a.minutes or 15
    enc = isochrone_polyline(st["lon"], st["lat"], minutes)
    path = path_overlay(enc, st["color"]) if enc else None
    percap = a.per or 8
    markers, counts = [], []
    for q, icon, color in CARD_SPOTLIGHTS:
        _, res = ws.search_station(st, q, limit=percap)
        counts.append((q, len(res)))
        for _, f in res:
            flon, flat = f["geometry"]["coordinates"]
            markers.append({"lon": flon, "lat": flat, "color": color, "size": "s", "icon": icon})
    markers.append({"lon": st["lon"], "lat": st["lat"], "color": st["color"], "size": "l", "icon": "rail-light"})
    vp = "auto" if path else "%s,%s,15,0" % (st["lon"], st["lat"])
    out = render(markers, vp, "light-v11", path)            # light basemap so the dots pop
    save_state({"lon": st["lon"], "lat": st["lat"]}, 15, "light-v11", markers, st["name"], path)
    summary = " · ".join("%d %s" % (n, q) for q, n in counts)
    print("%s  (%s — %d-min walkshed · %s)" % (out, st["name"], minutes, summary))
    print("  legend: restaurants=red, coffee=brown, bars=purple, parks=green; rail roundel = the station")


WALKSHEDS_SITE = os.environ.get("WALKSHEDS_SITE", "https://walksheds.xyz")
WALKSHOT_CACHE = os.path.join(HERE, "cache", "walkshots")


def _line_of(station):
    return "1" if "1" in station["lines"].split(",") else "2"


def deeplink(station, pois=None, walk=None):
    url = "%s/seattle/%s/%s" % (WALKSHEDS_SITE, _line_of(station), station["stopCode"])
    q = (["pois=" + ",".join(pois)] if pois else []) + (["walkshed=%d" % m for m in (walk or [])])
    return url + ("?" + "&".join(q) if q else "")


def walkshot_key(station, pois, walk):
    k = "%s-%s" % (_line_of(station), station["stopCode"])
    if pois:
        k += "__pois-" + "-".join(sorted(pois))
    if walk:
        k += "__walk-" + "-".join(str(m) for m in sorted(walk))
    return k


def do_walkshot(a):
    """Live walksheds.xyz screenshot at a station deeplink — served from cache when warm."""
    st = ws.resolve_station(a.station)
    if not st:
        sys.exit("no Link station matches %r — run: mapbot.py stations" % a.station)
    pois = [ws.QUERY_SYNONYMS.get(p.lower(), p.lower()) for p in re.split(r"[ ,]+", a.pois) if p] if a.pois else None
    walk = [int(x) for x in re.split(r"[ ,]+", a.walk) if x] if a.walk else None
    url = deeplink(st, pois, walk)
    out = os.path.join(OUT, "map.png")
    for e in ("png", "jpg"):
        f = os.path.join(OUT, "map.%s" % e)
        if os.path.exists(f):
            os.remove(f)
    cached = os.path.join(WALKSHOT_CACHE, walkshot_key(st, pois, walk) + ".png")
    source = "cached"
    if os.path.exists(cached) and not a.fresh:
        shutil.copyfile(cached, out)
    else:
        r = subprocess.run(["node", os.path.join(HERE, "webshot.mjs"), url,
                            str(a.width or 1000), str(a.height or 800)],
                           cwd=HERE, capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(out):
            sys.exit("walksheds screenshot failed: " + ((r.stderr or r.stdout) or "")[-300:])
        os.makedirs(WALKSHOT_CACHE, exist_ok=True)   # warm the cache for next time
        shutil.copyfile(out, cached)
        source = "live"
    save_state({"lon": st["lon"], "lat": st["lat"]}, 14, DEFAULT_STYLE,
               [{"lon": st["lon"], "lat": st["lat"], "color": st["color"], "size": "l"}], st["name"], None)
    print("%s  (%s%s — %s walksheds)" % (out, st["name"], (" · " + a.pois) if a.pois else "", source))
    print("  deeplink: %s" % url)


def do_arrivals(a):
    import transit
    st = ws.resolve_station(a.station)
    if st:
        stop = transit.link_stop_near(st["lat"], st["lon"]); name = st["name"]
    else:
        g = resolve(a.station)
        stops = transit.nearby_stops(g["lat"], g["lon"], 500)
        stop = stops[0] if stops else None; name = short(g["name"])
    if not stop:
        sys.exit("no transit stop found near %r" % a.station)
    deps, sits = transit.arrivals(stop["id"])
    print("Arrivals — %s (OBA stop %s)" % (name, stop["id"]))
    if not deps:
        print("  no upcoming departures in the next 45 min (late night / service may have ended)")
    for d in deps:
        when = "due" if d["mins"] <= 0 else "%d min" % d["mins"]
        print("  %-8s -> %-26s  %-7s %s" % (d["route"], (d["headsign"] or "")[:26], when,
                                            "live" if d["live"] else "sched"))
    for s in sits[:3]:
        msg = s["summary"] or s["desc"]
        if msg:
            print("  alert: %s" % msg[:140])


LINE_PILL = {"1": "38B030", "2": "00A0E0"}

BOARD_CSS = """<style>
 body{margin:0;background:#eef1f4;font-family:-apple-system,'Helvetica Neue',Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
 #board{width:520px;background:#fff;border-radius:16px;box-shadow:0 8px 30px rgba(20,30,40,.14);overflow:hidden;margin:16px}
 .hdr{display:flex;align-items:center;gap:9px;padding:18px 22px 4px}
 .pill{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;border-radius:12px;color:#fff;font-weight:700;font-size:14px;padding:0 8px}
 .stn{font-size:22px;font-weight:700;color:#19222b;letter-spacing:-.3px}
 .sub{padding:2px 22px 12px;color:#9aa3ac;font-size:11px;text-transform:uppercase;letter-spacing:.1em;font-weight:600}
 .row{display:flex;align-items:center;gap:13px;padding:13px 22px;border-top:1px solid #eef1f4}
 .badge{flex:0 0 auto;width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px}
 .dest{flex:1 1 auto;font-size:16px;color:#222b33;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .when{flex:0 0 auto;font-size:17px;font-weight:700;color:#19222b;min-width:64px;text-align:right}
 .when.due{color:#38B030}
 .live{display:inline-block;width:7px;height:7px;border-radius:50%;background:#38B030;margin-left:7px}
 .empty{padding:26px 22px;color:#9aa3ac;font-size:15px;text-align:center;border-top:1px solid #eef1f4;line-height:1.6}
 .empty-sub{font-size:12.5px;color:#b6bec6}
 .alert{display:flex;gap:9px;align-items:flex-start;padding:11px 22px;background:#fdf6e3;border-top:1px solid #f0e3bf;color:#7a601c;font-size:12.5px;line-height:1.4}
 .tri{flex:0 0 auto;margin-top:1px}
 .foot{padding:11px 22px 15px;color:#aeb6bd;font-size:10.5px;letter-spacing:.04em;border-top:1px solid #eef1f4}
</style>"""

_TRI = '<svg class="tri" width="15" height="15" viewBox="0 0 24 24"><path fill="#C99A2E" d="M12 2 1 21h22z"/><path fill="#fff" d="M11 9h2v6h-2zm0 8h2v2h-2z"/></svg>'


def build_board_html(station, lines, deps, alerts):
    import html as H
    esc = lambda s: H.escape(str(s or ""))
    pills = "".join('<span class="pill" style="background:#' + LINE_PILL.get(l.strip(), "4A5560") + '">'
                    + esc(l.strip()) + '</span>' for l in lines if l.strip())
    rows = []
    for d in deps:
        when = "Due" if d["mins"] <= 1 else (str(d["mins"]) + " min")
        live = '<span class="live"></span>' if d.get("live") else ""
        cls = "when due" if d["mins"] <= 1 else "when"
        rows.append('<div class="row"><span class="badge" style="background:#' + d.get("color", "4A5560")
                    + ';color:#' + d.get("text", "FFFFFF") + '">' + esc(d.get("badge", "?")) + '</span>'
                    + '<span class="dest">' + esc(d.get("headsign")) + '</span>'
                    + '<span class="' + cls + '">' + when + live + '</span></div>')
    body = "".join(rows) if rows else ('<div class="empty">No departures in the next 45 min<br>'
                                       '<span class="empty-sub">service may have ended for the night</span></div>')
    astrip = "".join('<div class="alert">' + _TRI + '<span>' + esc((al.get("summary") or al.get("desc") or "")[:160])
                     + '</span></div>' for al in alerts[:2] if (al.get("summary") or al.get("desc")))
    return ('<!DOCTYPE html><html><head><meta charset="utf-8">' + BOARD_CSS + '</head><body><div id="board">'
            + '<div class="hdr">' + pills + '<span class="stn">' + esc(station) + '</span></div>'
            + '<div class="sub">Departures &middot; live</div>' + body + astrip
            + '<div class="foot">OneBusAway &middot; Sound Transit / King County Metro</div>'
            + '</div></body></html>')


def do_board(a):
    import transit
    st = ws.resolve_station(a.station)
    if st:
        stop = transit.link_stop_near(st["lat"], st["lon"]); name = st["name"]; lines = st["lines"].split(",")
    else:
        g = resolve(a.station)
        stop = (transit.nearby_stops(g["lat"], g["lon"], 500) or [None])[0]; name = short(g["name"]); lines = []
    if not stop:
        sys.exit("no transit stop found near %r" % a.station)
    deps, sits = transit.arrivals(stop["id"], limit=7)
    if a.demo and not deps:
        deps = [{"route": "1 Line", "badge": "1", "color": "38B030", "text": "FFFFFF", "headsign": "Angle Lake", "mins": 3, "live": True},
                {"route": "1 Line", "badge": "1", "color": "38B030", "text": "FFFFFF", "headsign": "Lynnwood City Center", "mins": 8, "live": True},
                {"route": "2 Line", "badge": "2", "color": "00A0E0", "text": "FFFFFF", "headsign": "Downtown Redmond", "mins": 12, "live": False},
                {"route": "Route 8", "badge": "8", "color": "4A5560", "text": "FFFFFF", "headsign": "Seattle Center", "mins": 15, "live": True}]
    bf = os.path.join(OUT, "board.html")
    open(bf, "w").write(build_board_html(name, lines, deps, sits))
    out = os.path.join(OUT, "map.png")
    for e in ("png", "jpg"):
        f = os.path.join(OUT, "map.%s" % e)
        if os.path.exists(f):
            os.remove(f)
    r = subprocess.run(["node", os.path.join(HERE, "board.mjs"), "file://" + bf, out],
                       cwd=HERE, capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not os.path.exists(out):
        sys.exit("board render failed: " + ((r.stderr or r.stdout) or "")[-300:])
    save_state({"lon": stop["lon"], "lat": stop["lat"]}, 14, DEFAULT_STYLE, [], name, None)
    print("%s  (%s — departure board, %d departures, %d alerts)" % (out, name, len(deps), len(sits)))


def do_alerts(a):
    import transit
    if a.station:
        st = ws.resolve_station(a.station)
        if not st:
            sys.exit("no Link station matches %r — run: mapbot.py stations" % a.station)
        lines = ["%s Line" % l.strip() for l in st["lines"].split(",")]
        al = [x for x in transit.gtfs_rt_alerts() if any(L in x["routes"] for L in lines)]
        scope, narrow = "%s (%s)" % (st["name"], "/".join(lines)), ""
    else:
        al = transit.gtfs_rt_alerts(route_filter=a.route)
        scope = (" matching %r" % a.route) if a.route else ""
        narrow = "  ...and %d more — narrow with: alerts <route>"
    if not al:
        where = (" at %s" % st["name"]) if a.station else ((" for %r" % a.route) if a.route else "")
        print("No active service alerts%s (KC Metro + Sound Transit)." % where)
        return
    link = [x for x in al if any(r in ("1 Line", "2 Line") for r in x["routes"])]
    ordered = link + [x for x in al if x not in link]
    shown = ordered[: (a.limit or 8)]
    head = ("Active alerts at %s" % scope) if a.station else ("Active transit alerts%s" % scope)
    print("%s — %d total (KC Metro + Sound Transit):" % (head, len(al)))
    for x in shown:
        rts = ", ".join(x["routes"][:4]) or "system"
        print("  • [%s] %s" % (rts, (x["header"] or x["desc"] or "").strip()[:150]))
    if narrow and len(al) > len(shown):
        print(narrow % (len(al) - len(shown)))


def do_geo(a):
    """Semantic POI search over the transit-geo embedding index (cheap CLI tool result)."""
    import geo
    candidate, scope = None, "all Link stations"
    if a.at:
        st = ws.resolve_station(a.at)
        if st:
            candidate = geo.rows_in_tiles(ws.station_tiles(st["key"])); scope = "within a walk of %s" % st["name"]
        else:
            g = resolve_seattle(a.at)
            candidate = geo.rows_in_tiles(ws.tiles_around(g["lon"], g["lat"], 3)); scope = "near %s" % short(g["name"])
    res = geo.query(a.query, k=a.k or 8, candidate_rows=candidate)
    if not res:
        sys.exit("no matches for %r (%s)" % (a.query, scope))
    print("Semantic matches for %r — %s:" % (a.query, scope))
    for sc, m in res:
        print("  - %-34s %-13s [%.2f]  %.5f,%.5f"
              % ((m.get("name") or "?")[:34], (m.get("cat") or "")[:13], sc, m["lat"], m["lon"]))
    if a.plot:
        markers = [{"lon": m["lon"], "lat": m["lat"], "color": "ff2200", "size": "s"} for _, m in res]
        out = render(markers, "auto", load_state_style())
        save_state({"lon": res[0][1]["lon"], "lat": res[0][1]["lat"]}, 14, load_state_style(), markers, a.query)
        print("%s  (plotted %d)" % (out, len(res)))


def do_stations(a):
    by_line = {"1": [], "2": [], "1,2": []}
    for s in ws.stations():
        by_line.get(s["lines"], by_line["1,2"]).append(s["name"])
    print("Seattle Link stations (%d):" % len(ws.stations()))
    print("  1 Line only: " + ", ".join(by_line["1"]))
    print("  2 Line only: " + ", ".join(by_line["2"]))
    print("  Shared 1+2:  " + ", ".join(by_line["1,2"]))


def load_state_style():
    try:
        return load_state()["style"]
    except SystemExit:
        return DEFAULT_STYLE


def main():
    ap = argparse.ArgumentParser(prog="mapbot.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map"); m.add_argument("place"); m.add_argument("--zoom", type=int)
    m.add_argument("--style"); m.add_argument("--marker", action="append"); m.set_defaults(fn=do_map)
    z = sub.add_parser("zoom"); z.add_argument("direction", choices=["in", "out"])
    z.add_argument("--step", type=int); z.set_defaults(fn=do_zoom)
    pn = sub.add_parser("pan"); pn.add_argument("place"); pn.set_defaults(fn=do_pan)
    rs = sub.add_parser("restyle"); rs.add_argument("style"); rs.set_defaults(fn=do_restyle)
    nr = sub.add_parser("nearest"); nr.add_argument("query"); nr.add_argument("--around"); nr.set_defaults(fn=do_nearest)
    pl = sub.add_parser("plot"); pl.add_argument("query"); pl.add_argument("--at", required=True)
    pl.add_argument("--limit", type=int); pl.set_defaults(fn=do_plot)
    stn = sub.add_parser("station"); stn.add_argument("name"); stn.add_argument("--walk", type=int); stn.set_defaults(fn=do_station)
    wk = sub.add_parser("walkshed"); wk.add_argument("place"); wk.add_argument("minutes", nargs="?", type=int, default=15)
    wk.set_defaults(fn=do_walkshed)
    cd = sub.add_parser("card"); cd.add_argument("station"); cd.add_argument("--minutes", type=int)
    cd.add_argument("--per", type=int); cd.set_defaults(fn=do_card)
    wsh = sub.add_parser("walkshot"); wsh.add_argument("station"); wsh.add_argument("--pois")
    wsh.add_argument("--walk"); wsh.add_argument("--width", type=int); wsh.add_argument("--height", type=int)
    wsh.add_argument("--fresh", action="store_true", help="bypass cache, re-screenshot live")
    wsh.set_defaults(fn=do_walkshot)
    ar = sub.add_parser("arrivals"); ar.add_argument("station"); ar.set_defaults(fn=do_arrivals)
    bd = sub.add_parser("board"); bd.add_argument("station"); bd.add_argument("--demo", action="store_true")
    bd.set_defaults(fn=do_board)
    alp = sub.add_parser("alerts"); alp.add_argument("route", nargs="?"); alp.add_argument("--station")
    alp.add_argument("--limit", type=int); alp.set_defaults(fn=do_alerts)
    ge = sub.add_parser("geo"); ge.add_argument("query"); ge.add_argument("--at"); ge.add_argument("--k", type=int)
    ge.add_argument("--plot", action="store_true"); ge.set_defaults(fn=do_geo)
    sub.add_parser("stations").set_defaults(fn=do_stations)
    sub.add_parser("styles").set_defaults(fn=lambda a: print("Available basemaps (restyle <name> / map --style <name>):\n" +
        "\n".join("  %-24s %s" % (k, v) for k, v in STYLES.items())))
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
