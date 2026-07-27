"""Graph proximity — turn "the files you're editing" into a set of nearby symbols.

Given the files in play, seed with the symbols defined in them, then walk the code
graph (calls/inherits/contains, both directions) a couple of hops out. The result
is a symbol-name -> weight map the ranker uses to lift memories anchored to code
adjacent to your current edit, even when the prompt never names that code.

Weights decay by hop distance so the symbol under the cursor beats its callers'
callers. Keyed by symbol name (not id) because memories store symbol names — a
soft boost, so occasional name collisions across files are acceptable.
"""
from __future__ import annotations

from collections import defaultdict, deque

from ..store import Store

_HOP_WEIGHT = {0: 1.0, 1: 0.5, 2: 0.25}
_REL = ("calls", "inherits", "contains")


def context_from_files(store: Store, files, depth: int = 2) -> dict:
    """symbol-name -> proximity weight for symbols in/near `files`."""
    fileset = {f for f in (files or [])}
    if not fileset or not store.has_code_graph():
        return {}
    nodes, edges = store.code_graph()
    id2name = {n["id"]: n["name"] for n in nodes if n["kind"] != "file"}
    seeds = {nid for nid in id2name if nid.split("::")[0] in fileset}
    if not seeds:
        return {}

    adj: dict[str, set] = defaultdict(set)
    for e in edges:
        if e["rel"] in _REL:
            adj[e["src"]].add(e["dst"])
            adj[e["dst"]].add(e["src"])

    weights: dict[str, float] = {}
    best_depth: dict[str, int] = {s: 0 for s in seeds}
    q: deque = deque((s, 0) for s in seeds)
    while q:
        cur, d = q.popleft()
        if d > best_depth.get(cur, d):
            continue
        nm = id2name.get(cur)
        if nm:
            weights[nm] = max(weights.get(nm, 0.0), _HOP_WEIGHT.get(d, 0.0))
        if d >= depth:
            continue
        for nb in adj.get(cur, ()):
            nd = d + 1
            if nb not in best_depth or nd < best_depth[nb]:
                best_depth[nb] = nd
                q.append((nb, nd))
    return weights
