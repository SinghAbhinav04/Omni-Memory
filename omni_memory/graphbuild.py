"""Assemble the knowledge graph for the dashboard from stored memory + git.

P0 builds nodes/edges from memories, files, branches and commits. A later phase
augments this with a tree-sitter AST code graph (see graph/ package).
"""
from __future__ import annotations

from pathlib import Path

from .store import Store


def build_graph(store: Store, root: Path) -> dict:
    """Assemble the *knowledge* graph for the dashboard's Memory Graph tab:
    memories, the files they touch, and branches, linked into {nodes, edges}.
    (Distinct from the *code* graph in `graph/build.py`.)"""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid: str, label: str, kind: str, **extra):
        nodes.setdefault(nid, {"id": nid, "label": label, "kind": kind, **extra})

    for b in store.branches():
        node(f"branch:{b['name']}", b["name"], "branch",
             creator=b.get("creator"), status=b.get("status"))
        if b.get("base_branch"):
            node(f"branch:{b['base_branch']}", b["base_branch"], "branch")
            edges.append({"from": f"branch:{b['name']}",
                          "to": f"branch:{b['base_branch']}", "rel": "branched-from"})

    for m in store.memories(status="active", limit=1000):
        mid = f"mem:{m['id']}"
        node(mid, m["text"][:48], "memory", kind_detail=m["kind"], branch=m["branch"])
        edges.append({"from": f"branch:{m['branch']}", "to": mid, "rel": "remembers"})
        for f in m["files"]:
            fid = f"file:{f}"
            node(fid, f.split("/")[-1], "file", path=f)
            edges.append({"from": mid, "to": fid, "rel": "about"})

    return {"nodes": list(nodes.values()), "edges": edges}
