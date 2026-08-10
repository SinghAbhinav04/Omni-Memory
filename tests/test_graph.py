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


def test_empty_extract_keeps_previous_graph(store, repo):
    codegraph.build_code_graph(store, repo)
    assert store.has_code_graph()
    n = len(store.code_graph()[0])
    (repo / "svc.py").unlink()                     # nothing parseable left
    extract._EXTRACT_CACHE.clear()
    r = codegraph.build_code_graph(store, repo)
    assert r.get("kept_previous")                  # did NOT wipe
    assert len(store.code_graph()[0]) == n         # previous graph intact


def test_build_command_builds_code_graph(repo, monkeypatch):
    import types
    import omni_memory.cli as cli
    monkeypatch.chdir(repo)
    cli.cmd_build(types.SimpleNamespace(no_ai=True, no_docs=True))
    from omni_memory.store import Store
    assert len(Store(repo).code_graph()[0]) > 0    # first build populates it


def test_only_project_source_graphed(tmp_path):
    """Build output, dependencies, and minified bundles must be excluded — only
    the developer's own (git-tracked, non-ignored) source is graphed."""
    import subprocess
    d = tmp_path / "proj"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    (d / "app.py").write_text("def handler():\n    return svc()\ndef svc():\n    return 1\n")
    (d / ".gitignore").write_text(".omni-memory/\n.next/\nnode_modules/\n")
    (d / ".next").mkdir(); (d / ".next" / "chunk.js").write_text("function asyncModule(){}\n")
    (d / "node_modules").mkdir(); (d / "node_modules" / "l.js").write_text("function vendored(){}\n")
    (d / "b.min.js").write_text("function a(){}" * 500 + "\n")
    subprocess.run(["git", "-C", str(d), "add", "app.py", ".gitignore", "b.min.js"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "i"], check=True, capture_output=True)
    extract._EXTRACT_CACHE.clear()
    fx = extract.extract_repo(d)
    names = {s["name"] for s in fx["symbols"] if s["kind"] in ("function", "method", "class")}
    assert {"handler", "svc"} <= names             # real source in
    assert "asyncModule" not in names              # .next build output out
    assert "vendored" not in names                 # node_modules out
    assert "a" not in names                         # minified bundle out


def test_js_regex_extraction():
    """The stdlib JS/TS backend (no tree-sitter) graphs functions, classes,
    methods, calls, and throws — so the plugin works on a TypeScript repo."""
    ts = (
        "export async function createClaim(user, amount) {\n"
        "  if (!amount) throw new ValidationError('no amount');\n"
        "  return insertClaim(user, amount);\n"
        "}\n"
        "function insertClaim(u, a) { return {id: 1}; }\n"
        "class ClaimService extends BaseService {\n"
        "  async process(id) {\n"
        "    return createClaim('x', 1);\n"
        "  }\n"
        "}\n")
    fx = extract._extract_js_regex("svc.ts", ts)
    by = {s["name"]: s for s in fx["symbols"]}
    assert {"createClaim", "insertClaim", "ClaimService", "process"} <= set(by)
    assert by["process"]["kind"] == "method"
    assert by["process"]["parent"].endswith("::ClaimService")
    assert "ValidationError" in by["createClaim"]["raises"]
    calls = {(c["src"].split("::")[-1], c["name"]) for c in fx["calls"]}
    assert ("createClaim", "insertClaim") in calls
    assert {"cls": "svc.ts::ClaimService", "name": "BaseService"} in fx["bases"]


def test_ts_repo_gets_code_graph(tmp_path):
    """End-to-end: a git-tracked .ts repo yields a non-empty code graph via the
    stdlib backend (build.py drops the external base but keeps in-repo edges)."""
    import subprocess
    d = tmp_path / "ts"
    d.mkdir()
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    (d / "a.ts").write_text("export function f(){ return g(); }\nfunction g(){ return 1; }\n")
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "i"], check=True, capture_output=True)
    extract._EXTRACT_CACHE.clear()
    from omni_memory.store import Store
    s = Store(d)
    codegraph.build_code_graph(s, d)
    names = {n["name"] for n in s.code_graph()[0] if n["kind"] != "file"}
    assert {"f", "g"} <= names


def test_reconcile_flags_orphaned_source(store, repo):
    """A memory whose anchored file was DELETED at source is flagged orphaned
    (source-diff reconciliation), while a live-anchor memory stays fresh."""
    import subprocess
    from omni_memory import staleness, branch as branchmod
    from omni_memory.store import Memory
    (repo / "gone.py").write_text("def dead():\n    return 2\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "add gone"], check=True, capture_output=True)
    branchmod.full_refresh(store, repo)
    live = store.add_memory(Memory(text="Service.create_order builds the row", kind="flow",
                                   branch="main", files=["svc.py"], symbols=["create_order"]))
    orphan = store.add_memory(Memory(text="dead() returns 2", kind="fact",
                                     branch="main", files=["gone.py"], symbols=["dead"]))
    (repo / "gone.py").unlink()
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "rm gone"], check=True, capture_output=True)
    from omni_memory.graph import extract as _ex
    _ex._EXTRACT_CACHE.clear()
    branchmod.full_refresh(store, repo)
    rec = staleness.reconcile(store, repo)
    assert rec["orphaned"] >= 1
    assert store.get_memory(orphan.id)["stale"]        # deleted source → orphaned
    assert not store.get_memory(live.id)["stale"]      # live anchor → fresh
    assert 0 < rec["coverage"] < 1                      # measured re-fetchable ratio


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
