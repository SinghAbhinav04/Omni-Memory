"""Memory Health Galaxy — per-file rollup for the dashboard's health map.

Every source file is a node, clustered into module "galaxies" by its package path.
Its color is a single memory-health score in [0,1] (warm = under-remembered / stale,
cool = well-remembered + fresh + trusted); files with no memory at all are blind
spots (score = None) so the un-remembered regions show up as dark gaps.

This is pure visualization over data we already have — the code graph plus each
memory's staleness/evidence/citation — so there's no new capture or schema.
"""
from __future__ import annotations

from pathlib import Path

from .store import Store

_EV_W = {"verified": 1.0, "stated": 0.6, "inferred": 0.3}
_MAX_NODES = 2000
_MAX_EDGES = 300


def _cluster(path: str) -> str:
    parts = path.replace("\\", "/").split("/")
    if len(parts) == 1:
        return "(root)"
    return "/".join(parts[:2]) if len(parts) > 2 else parts[0]


def _score(mem: int, stale: int, trust_sum: float, cited: int) -> float:
    """Blend freshness, trust, citation, and coverage into one [0,1] health score."""
    fresh = (mem - stale) / mem
    trust = trust_sum / mem
    cite = min(1.0, cited / mem)
    cov = 1.0 - 0.6 ** mem                       # more memory on a file → better covered
    return max(0.0, min(1.0, 0.45 * fresh + 0.30 * trust + 0.15 * cite + 0.10 * cov))


def build(store: Store, root: Path) -> dict:
    nodes_raw, edges_raw = store.code_graph()
    sym_file = {n["id"]: n["file"] for n in nodes_raw if n["kind"] != "file"}
    sym_count: dict[str, int] = {}
    for n in nodes_raw:
        if n["kind"] != "file":
            sym_count[n["file"]] = sym_count.get(n["file"], 0) + 1
    # Files are keyed by the relative path the symbols + memories use. (The graph's
    # own file nodes carry bare basenames, so they're not used here — mixing the two
    # produced phantom duplicate nodes.)
    file_paths = set(sym_count)

    # roll each active memory up onto the files it's anchored to
    agg: dict[str, dict] = {}
    for m in store.memories(status="active", limit=1_000_000):
        for f in (m.get("files") or []):
            a = agg.setdefault(f, {"mem": 0, "stale": 0, "trust": 0.0, "cited": 0,
                                   "verified": 0, "protected": 0})
            a["mem"] += 1
            a["stale"] += 1 if m.get("stale") else 0
            a["trust"] += _EV_W.get(m.get("evidence"), 0.6)
            a["cited"] += 1 if (m.get("uses") or 0) > 0 else 0
            a["verified"] += 1 if m.get("evidence") == "verified" else 0
            a["protected"] += 1 if m.get("protected") else 0
            file_paths.add(f)

    nodes = []
    for path in file_paths:
        a = agg.get(path)
        on_disk = (root / path).exists()
        if not a:                                # no memory → blind spot
            verdict, score = "blind", None
        elif not on_disk:                        # anchored to a deleted file
            verdict, score = "orphaned", 0.08
        else:
            score = round(_score(a["mem"], a["stale"], a["trust"], a["cited"]), 3)
            verdict = "drifted" if a["stale"] else "fresh"
        nodes.append({
            "id": path, "cluster": _cluster(path),
            "size": sym_count.get(path, 1), "score": score, "verdict": verdict,
            "mem": a["mem"] if a else 0, "stale": a["stale"] if a else 0,
            "verified": a["verified"] if a else 0,
            "protected": bool(a["protected"]) if a else False})
    # cap: keep everything with memory + the largest blind files
    if len(nodes) > _MAX_NODES:
        nodes.sort(key=lambda n: (n["mem"] > 0, n["size"]), reverse=True)
        nodes = nodes[:_MAX_NODES]
    keep = {n["id"] for n in nodes}

    # file→file call edges (aggregate symbol calls up to the file level)
    ew: dict[tuple, int] = {}
    for e in edges_raw:
        if e.get("rel") != "calls":
            continue
        sf, df = sym_file.get(e["src"]), sym_file.get(e["dst"])
        if sf and df and sf != df and sf in keep and df in keep:
            ew[(sf, df)] = ew.get((sf, df), 0) + 1
    edges = [{"src": s, "dst": d, "w": w}
             for (s, d), w in sorted(ew.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_EDGES]]

    clusters = sorted({n["cluster"] for n in nodes})
    lit = [n for n in nodes if n["score"] is not None]
    stats = {"files": len(nodes), "remembered": len(lit),
             "blind": sum(1 for n in nodes if n["verdict"] == "blind"),
             "drifted": sum(1 for n in nodes if n["verdict"] == "drifted"),
             "orphaned": sum(1 for n in nodes if n["verdict"] == "orphaned"),
             "avg_score": round(sum(n["score"] for n in lit) / len(lit), 3) if lit else 0.0}
    return {"nodes": nodes, "edges": edges, "clusters": clusters, "stats": stats}
