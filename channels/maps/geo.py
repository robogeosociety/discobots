"""Transit-geo semantic embeddings for MapBot (the tommybot geo pattern, CLI-cheap).

Embeds the local walksheds POIs with BAAI/bge-small-en-v1.5 via fastembed (ONNX/CPU — the same
model tommybot uses, but no torch/MPS, so it loads in ~1s for a one-shot CLI call). Vectors are
stored once (cache/geo/), and a query does exact brute-force cosine, optionally pre-filtered to a
station's walkshed tiles so "X near station Y" stays precise. Sonnet calls this as a cheap tool.
"""
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "vendor"))   # numpy/fastembed/onnxruntime live here

import numpy as np  # noqa: E402

import walksheds as ws  # noqa: E402

GEO_DIR = os.path.join(HERE, "cache", "geo")
VEC = os.path.join(GEO_DIR, "vecs.npy")
META = os.path.join(GEO_DIR, "meta.json")
MODEL = "BAAI/bge-small-en-v1.5"
# bge retrieval works best with this query instruction; corpus docs are embedded bare.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_emb = None
_idx = None


def _embedder():
    global _emb
    if _emb is None:
        from fastembed import TextEmbedding
        _emb = TextEmbedding(model_name=MODEL)
    return _emb


def _doc(p):
    parts = [p.get("name") or ""]
    if p.get("category"):
        parts.append(p["category"])
    tags = p.get("tags") or []
    if tags:
        parts.append(", ".join(tags))
    return ". ".join(x for x in parts if x)


def _norm(v):
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def build_index():
    metas, docs, seen = [], [], set()
    for f in sorted(glob.glob(os.path.join(ws.TILES, "*.geojson"))):
        for ft in json.load(open(f))["features"]:
            p = ft["properties"]; fid = p.get("id")
            if fid in seen:
                continue
            seen.add(fid)
            lon, lat = ft["geometry"]["coordinates"]
            metas.append({"id": fid, "name": p.get("name"), "cat": p.get("category"),
                          "lon": lon, "lat": lat, "tags": (p.get("tags") or [])[:8]})
            docs.append(_doc(p))
    vecs = _norm(np.array(list(_embedder().embed(docs)), dtype=np.float32))
    os.makedirs(GEO_DIR, exist_ok=True)
    np.save(VEC, vecs.astype(np.float16))
    json.dump(metas, open(META, "w"))
    return len(metas)


def _load():
    global _idx
    if _idx is None:
        if not os.path.exists(VEC):
            raise SystemExit("geo index not built yet — run cache_geo.py")
        vecs = np.load(VEC).astype(np.float32)
        metas = json.load(open(META))
        _idx = (vecs, metas, {m["id"]: i for i, m in enumerate(metas)})
    return _idx


def rows_in_tiles(tile_keys):
    _, _, idbyrow = _load()
    rows = []
    for tk in tile_keys:
        for ft in ws._tile(tk):
            fid = ft["properties"].get("id")
            if fid in idbyrow:
                rows.append(idbyrow[fid])
    return rows


def query(text, k=8, candidate_rows=None):
    vecs, metas, _ = _load()
    qv = _norm(np.array(list(_embedder().embed([QUERY_PREFIX + text]))[0], dtype=np.float32))
    if candidate_rows is not None:
        if not candidate_rows:
            return []
        rows = np.array(sorted(set(candidate_rows)))
        scores = vecs[rows] @ qv
        order = np.argsort(scores)[::-1][:k]
        return [(float(scores[o]), metas[int(rows[o])]) for o in order]
    scores = vecs @ qv
    order = np.argsort(scores)[::-1][:k]
    return [(float(scores[o]), metas[int(o)]) for o in order]
