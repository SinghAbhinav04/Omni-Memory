"""Assemble + persist the code graph from extracted symbols.

Takes extract.py's raw (name-based) output and resolves calls/bases to concrete
symbol ids, producing a directed graph:
  contains  parent  -> child symbol
  calls     caller  -> callee (resolved within the repo; external calls dropped)
  inherits  class   -> base class

Only intra-repo edges survive resolution — that's exactly what staleness needs
to propagate a change to its dependents. Persisted to SQLite so it's incremental.
"""
from __future__ import annotations

from pathlib import Path

from ..store import Store
from . import extract


def build_code_graph(store: Store, root: Path) -> dict:
    """Extract the repo's symbols, resolve their calls/bases to concrete symbol
    ids, and persist the directed code graph. Returns a summary (backend, files
    parsed, node/edge/kind counts) for the CLI."""
    fx = extract.extract_repo(root)
    nodes, edges = _assemble(fx)
    # Never clobber a good graph with an empty one: a transient read error or a
    # file set the backend can't parse (e.g. a non-Python repo with tree-sitter
    # absent) would otherwise wipe the Code Graph to 0. Keep the previous one.
    real = [n for n in nodes if n["kind"] != "file"]
    if not real and store.has_code_graph():
        return {"backend": fx["backend"], "files_parsed": fx["files_parsed"],
                "nodes": 0, "edges": 0, "kinds": {}, "rels": {}, "kept_previous": True}
    store.replace_code_graph(nodes, edges)
    kinds = {}
    for n in nodes:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    rels = {}
    for e in edges:
        rels[e["rel"]] = rels.get(e["rel"], 0) + 1
    return {"backend": fx["backend"], "files_parsed": fx["files_parsed"],
            "nodes": len(nodes), "edges": len(edges), "kinds": kinds, "rels": rels}


def _assemble(fx: dict) -> tuple[list[dict], list[dict]]:
    nodes = fx["symbols"]
    by_name: dict[str, list[dict]] = {}
    for s in nodes:
        if s["kind"] in ("function", "method", "class"):
            by_name.setdefault(s["name"], []).append(s)

    seen: set[tuple] = set()
    edges: list[dict] = []

    def add(src: str, dst: str, rel: str):
        key = (src, dst, rel)
        if src and dst and src != dst and key not in seen:
            seen.add(key)
            edges.append({"src": src, "dst": dst, "rel": rel})

    # attach the raw callee names per symbol (incl. external/unresolved ones,
    # e.g. kafka.publish) so the dossier can show real downstream + emits.
    raw_calls: dict[str, list[str]] = {}
    for c in fx["calls"]:
        lst = raw_calls.setdefault(c["src"], [])
        if c["name"] not in lst:
            lst.append(c["name"])
    for s in nodes:
        s["calls"] = raw_calls.get(s["id"], [])

    for s in nodes:
        if s.get("parent"):
            add(s["parent"], s["id"], "contains")
    for c in fx["calls"]:
        add(c["src"], _resolve(c["name"], c["src"], by_name), "calls")
    for b in fx["bases"]:
        add(b["cls"], _resolve(b["name"], b["cls"], by_name), "inherits")
    return nodes, edges


def _resolve(name: str, src_id: str, by_name: dict[str, list[dict]]) -> str:
    """Resolve a called/base name to a symbol id. Prefer a same-file definition;
    else a unique global match; else give up (ambiguous/external -> no edge)."""
    cands = by_name.get(name)
    if not cands:
        return ""
    if len(cands) == 1:
        return cands[0]["id"]
    src_file = src_id.split("::")[0]
    same = [c for c in cands if c["file"] == src_file]
    if len(same) == 1:
        return same[0]["id"]
    return ""
