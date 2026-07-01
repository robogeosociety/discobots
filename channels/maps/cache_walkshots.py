#!/usr/bin/env python3
"""Pre-render walksheds.xyz station screenshots into cache/walkshots/ so `walkshot` is instant.

Default: the base (no-filter) view for every Link station. Add --filters to also cache per
category (e.g. coffee,restaurants,bars,parks). Writes a manifest (cache/walkshots/index.json).

Usage:
  cache_walkshots.py [--filters coffee,restaurants,bars,parks] [--walk 5,10]
                     [--workers 3] [--width 1000] [--height 800]
"""
import argparse, concurrent.futures as cf, json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import walksheds as ws

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache", "walkshots")
os.makedirs(CACHE, exist_ok=True)
SITE = os.environ.get("WALKSHEDS_SITE", "https://walksheds.xyz")


def line_of(st):
    return "1" if "1" in st["lines"].split(",") else "2"


def deeplink(st, pois, walk):
    url = "%s/seattle/%s/%s" % (SITE, line_of(st), st["stopCode"])
    q = (["pois=" + ",".join(pois)] if pois else []) + (["walkshed=%d" % m for m in (walk or [])])
    return url + ("?" + "&".join(q) if q else "")


def cache_key(st, pois, walk):
    k = "%s-%s" % (line_of(st), st["stopCode"])
    if pois:
        k += "__pois-" + "-".join(sorted(pois))
    if walk:
        k += "__walk-" + "-".join(str(m) for m in sorted(walk))
    return k


def shoot(job):
    st, pois, walk, W, H = job
    url = deeplink(st, pois, walk)
    k = cache_key(st, pois, walk)
    out = os.path.join(CACHE, k + ".png")
    try:
        r = subprocess.run(["node", os.path.join(HERE, "webshot.mjs"), url, str(W), str(H), out],
                           cwd=HERE, capture_output=True, text=True, timeout=150)
        ok = r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 1000
        err = "" if ok else (r.stderr or r.stdout or "")[-160:]
    except Exception as e:
        ok, err = False, repr(e)[-160:]
    return {"key": k, "station": st["name"], "deeplink": url,
            "file": "cache/walkshots/%s.png" % k, "ok": ok, "err": err}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filters")
    ap.add_argument("--walk")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--width", type=int, default=1000)
    ap.add_argument("--height", type=int, default=800)
    a = ap.parse_args()

    views = [None]  # base view always
    if a.filters:
        views += [ws.QUERY_SYNONYMS.get(f.strip().lower(), f.strip().lower())
                  for f in a.filters.split(",") if f.strip()]
    walk = [int(x) for x in a.walk.split(",")] if a.walk else None

    jobs = [(st, ([v] if v else None), walk, a.width, a.height)
            for st in ws.stations() for v in views]
    print("building %d shots (%d stations x %d views) workers=%d -> %s"
          % (len(jobs), len(ws.stations()), len(views), a.workers, CACHE))

    manifest, done, t0 = {}, 0, time.time()
    with cf.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(shoot, jobs):
            done += 1
            manifest[res["key"]] = res
            print("[%d/%d] %s %s%s" % (done, len(jobs), "ok  " if res["ok"] else "FAIL",
                                       res["key"], "" if res["ok"] else "  :: " + res["err"]))
    json.dump({"built_at": int(time.time()), "site": SITE, "views": views, "shots": manifest},
              open(os.path.join(CACHE, "index.json"), "w"), indent=1)
    ok = sum(1 for v in manifest.values() if v["ok"])
    print("done: %d/%d ok in %ds -> %s" % (ok, len(jobs), int(time.time() - t0), CACHE))


if __name__ == "__main__":
    main()
