"""Code graph: extraction fields, incremental cache, build resolution, dossier,
and symbol-level staleness."""
from pathlib import Path

from omni_memory.graph import extract, build as codegraph


def _sym(fx, name):
    return next(s for s in fx["symbols"] if s["name"] == name)


def test_extract_fields(repo):
    fx = extract.extract_file(repo / "svc.py", repo)
    co = _sym(fx, "create_order")
    assert co["kind"] == "method"
    assert co["signature"].startswith("(self, user, items)")
    assert "order" in co["doc"].lower()
    assert "ValidationError" in co["raises"]


def test_is_emit():
    assert extract.is_emit("publish") and extract.is_emit("emitEvent")
    assert not extract.is_emit("insert")


def test_incremental_cache_reparses_only_changed(repo):
    r1 = extract.extract_repo(repo)
    assert r1["reparsed"] == r1["files_parsed"] >= 1
    r2 = extract.extract_repo(repo)
    assert r2["reparsed"] == 0                      # nothing changed
    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\ndef added():\n    return 0\n")
    r3 = extract.extract_repo(repo)
    assert r3["reparsed"] == 1                      # exactly the touched file


def test_build_resolves_calls_and_inheritance(store, repo):
    summary = codegraph.build_code_graph(store, repo)
    assert summary["nodes"] > 0
    nodes, edges = store.code_graph()
    by_id = {n["id"]: n for n in nodes}
    # create_order → _insert / _publish resolved as calls
    calls = {(e["src"], e["dst"]) for e in edges if e["rel"] == "calls"}
    co = next(n["id"] for n in nodes if n["name"] == "create_order")
    ins = next(n["id"] for n in nodes if n["name"] == "_insert")
    assert (co, ins) in calls
    # Service inherits Base
    inh = {(e["src"], e["dst"]) for e in edges if e["rel"] == "inherits"}
    svc = next(n["id"] for n in nodes if n["name"] == "Service")
    base = next(n["id"] for n in nodes if n["name"] == "Base")
    assert (svc, base) in inh
    assert by_id  # sanity


def test_symbol_dossier(store, repo):
    codegraph.build_code_graph(store, repo)
    co_id = next(n["id"] for n in store.code_graph()[0] if n["name"] == "create_order")
    d = store.symbol_dossier(co_id)
    assert d["signature"].startswith("(self, user, items)")
    assert "ValidationError" in d["raises"]
    names_out = {c["name"] for c in d["calls_out"]}
    assert {"_insert", "_publish"} <= names_out
    # raw calls keep everything incl. the external ValidationError constructor
    assert {"_insert", "_publish"} <= set(d["calls"])


def test_staleness_flags_changed_symbol(store, repo):
    """Anchor a memory to create_order at commit A, change that function and commit
    B; recompute must flag it stale (git diff A..B hunk ∩ symbol range)."""
    import subprocess
    from omni_memory import branch as branchmod, staleness
    from omni_memory.store import Memory

    def git(*a):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)

    branchmod.full_refresh(store, repo)
    anchor = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    # symbols are matched by bare name within the memory's files
    store.add_memory(Memory(text="create_order builds the row then publishes",
                            kind="flow", branch="main", files=["svc.py"],
                            symbols=["create_order"], commit_range=anchor))
    src = (repo / "svc.py").read_text().replace(
        "row = self._insert(user, items)",
        "row = self._insert(user, items)  # behavior changed")
    (repo / "svc.py").write_text(src)
    git("commit", "-aqm", "change create_order")     # staleness diffs committed history
    codegraph.build_code_graph(store, repo)
    staleness.recompute(store, repo)
    stale = store.db.execute(
        "SELECT COUNT(*) n FROM memory WHERE status='active' AND stale=1").fetchone()["n"]
    assert stale >= 1
