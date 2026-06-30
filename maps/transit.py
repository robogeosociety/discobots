"""Live transit enrichment for MapBot — OneBusAway Puget Sound.

- Real-time arrivals/departures via the OBA REST API (stdlib urllib).
- Agency service alerts via the GTFS-Realtime feed (the same source the #transit discobot
  watches), parsed with vendored gtfs-realtime-bindings.
- Route names/colors from the local static GTFS at /Volumes/dev/transit_tracker/data/gtfs.

The OBA key is read from the transit_tracker service.yaml (falls back to OBA_API_KEY / TEST).
"""
import csv
import functools
import json
import os
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))   # vendored gtfs-realtime-bindings

OBA = "https://api.pugetsound.onebusaway.org/api/where"
OBA_RT = "https://api.pugetsound.onebusaway.org/api/gtfs_realtime"
GTFS_DIR = "/Volumes/dev/transit_tracker/data/gtfs"
AGENCIES = ["1", "40"]                              # 1 = KC Metro, 40 = Sound Transit
LINK_ROUTES = {"40_100479": "1 Line", "40_2LINE": "2 Line"}
LINK_COLOR = {"1 Line": "28813F", "2 Line": "007CAD"}


def _key():
    svc = os.path.expanduser("~/dev/transit_tracker/.local/service.yaml")
    try:
        for line in open(svc):
            if line.strip().startswith("oba_api_key:"):
                v = line.split(":", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except Exception:
        pass
    return os.environ.get("OBA_API_KEY", "TEST")


KEY = _key()


def _open(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MapBot/1"}), timeout=20)


def oba(path, **params):
    params["key"] = KEY
    return json.load(_open("%s%s?%s" % (OBA, path, urllib.parse.urlencode(params))))["data"]


@functools.lru_cache(1)
def route_meta():
    """route_id ('40_100479') -> {name, badge, color, text} from local static GTFS."""
    m = {}
    for ag in ("1", "40", "95"):
        p = os.path.join(GTFS_DIR, ag, "routes.txt")
        if not os.path.exists(p):
            continue
        for row in csv.DictReader(open(p)):
            short = (row.get("route_short_name") or "").strip()
            m["%s_%s" % (ag, row["route_id"])] = {
                "name": short or row.get("route_long_name") or row["route_id"],
                "badge": short or (row.get("route_long_name") or "?")[:3],
                "color": (row.get("route_color") or "").strip() or None,
                "text": (row.get("route_text_color") or "").strip() or "FFFFFF",
            }
    return m


def route_names():
    return {k: v["name"] for k, v in route_meta().items()}


def nearby_stops(lat, lon, radius=450):
    return oba("/stops-for-location.json", lat=lat, lon=lon, radius=radius).get("list", [])


def link_stop_near(lat, lon, radius=600):
    best = None
    for s in nearby_stops(lat, lon, radius):
        if any(r in LINK_ROUTES for r in s.get("routeIds", [])):
            d = (s["lat"] - lat) ** 2 + (s["lon"] - lon) ** 2
            if best is None or d < best[0]:
                best = (d, s)
    return best[1] if best else None


def arrivals(stop_id, minutes=45, limit=8):
    """(departures, situations) for a stop. departures sorted by minutes away."""
    d = oba("/arrivals-and-departures-for-stop/%s.json" % stop_id, minutesAfter=minutes)
    now = time.time()
    meta = route_meta()
    # Walksheds-style Link palette (brighter than the GTFS values) for the line roundels.
    LINK = {"40_100479": ("1", "38B030"), "40_2LINE": ("2", "00A0E0")}
    deps = []
    for a in d.get("arrivalsAndDepartures", []):
        t = a.get("predictedDepartureTime") or a.get("predictedArrivalTime") \
            or a.get("scheduledDepartureTime") or a.get("scheduledArrivalTime") or 0
        if not t:
            continue
        rid = a.get("routeId")
        m = meta.get(rid, {})
        badge, color = m.get("badge", "?"), m.get("color") or "4A5560"
        if rid in LINK:
            badge, color = LINK[rid]
        deps.append({"route": a.get("routeShortName") or m.get("name") or rid,
                     "badge": badge, "color": color, "text": m.get("text", "FFFFFF"),
                     "headsign": a.get("tripHeadsign"), "mins": int(round((t / 1000 - now) / 60)),
                     "live": bool(a.get("predicted"))})
    deps.sort(key=lambda x: x["mins"])
    sits = (d.get("references", {}) or {}).get("situations", []) or d.get("situations", []) or []
    out_sits = []
    for s in sits:
        out_sits.append({"summary": _tr_oba(s.get("summary")), "desc": _tr_oba(s.get("description"))})
    return deps[:limit], out_sits


def _tr_oba(v):
    if isinstance(v, dict):
        return v.get("value") or ""
    return v or ""


# --- GTFS-Realtime agency alerts (protobuf) ----------------------------------------
def _tr_rt(translated):
    try:
        return translated.translation[0].text if translated.translation else ""
    except Exception:
        return ""


def gtfs_rt_alerts(route_filter=None):
    """Active service alerts across agencies 1+40. route_filter: case-insensitive substring
    on a route's friendly name (e.g. '1 line', '7'). Returns list of dicts."""
    from google.transit import gtfs_realtime_pb2
    rn = route_names()
    out = []
    for ag in AGENCIES:
        url = "%s/alerts-for-agency/%s.pb?key=%s" % (OBA_RT, ag, KEY)
        try:
            raw = _open(url).read()
        except Exception:
            continue
        feed = gtfs_realtime_pb2.FeedMessage()
        try:
            feed.ParseFromString(raw)
        except Exception:
            continue
        for e in feed.entity:
            if not e.HasField("alert"):
                continue
            al = e.alert
            routes = set()
            for ie in al.informed_entity:
                if ie.route_id:
                    k = "%s_%s" % (ie.agency_id or ag, ie.route_id)
                    routes.add(rn.get(k, ie.route_id))
            eff = gtfs_realtime_pb2.Alert.Effect.Name(al.effect) if al.effect else ""
            out.append({"header": _tr_rt(al.header_text), "desc": _tr_rt(al.description_text),
                        "effect": eff, "routes": sorted(routes)})
    if route_filter:
        import re as _re
        rf = route_filter.lower().strip()
        rf = {"link": "line", "1": "1 line", "2": "2 line"}.get(rf, rf)
        pat = _re.compile(r"\b" + _re.escape(rf) + r"\b")

        def _hit(a):
            if any(rf == r.lower() or pat.search(r.lower()) for r in a["routes"]):
                return True
            return len(rf) >= 4 and rf in a["header"].lower()   # word filters can match the headline
        out = [a for a in out if _hit(a)]
    # de-dupe by header
    seen, uniq = set(), []
    for a in out:
        if a["header"] in seen:
            continue
        seen.add(a["header"]); uniq.append(a)
    return uniq
