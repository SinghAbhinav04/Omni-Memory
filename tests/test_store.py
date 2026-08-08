"""Store: memory CRUD, search, dedup/merge, re-anchor, flush, overview."""
from omni_memory.store import Memory


def _add(store, text, kind="fact", branch="main", files=None):
    return store.add_memory(Memory(text=text, kind=kind, branch=branch, files=files or []))


def test_add_and_get(store):
    m = _add(store, "Auth uses JWT in an httpOnly cookie", kind="decision", files=["a.py"])
    got = store.get_memory(m.id)
    assert got["text"] == "Auth uses JWT in an httpOnly cookie"
    assert got["kind"] == "decision"
    assert got["files"] == ["a.py"]
    assert got["status"] == "active"


def test_update_memory(store):
    m = _add(store, "original")
    assert store.update_memory(m.id, text="edited", kind="gotcha", files=["b.py"])
    got = store.get_memory(m.id)
    assert got["text"] == "edited" and got["kind"] == "gotcha" and got["files"] == ["b.py"]


def test_update_only_given_fields(store):
    m = _add(store, "keep text", kind="fact")
    store.update_memory(m.id, kind="todo")           # text untouched
    got = store.get_memory(m.id)
    assert got["text"] == "keep text" and got["kind"] == "todo"


def test_forget_archives(store):
    m = _add(store, "temporary")
    assert store.forget(m.id)
    assert store.get_memory(m.id)["status"] == "abandoned"


def test_search_hits_memory_text(store):
    _add(store, "the payment gateway uses Stripe PaymentIntents")
    _add(store, "unrelated note about caching")
    res = store.search("stripe")
    assert any("Stripe" in m["text"] for m in res["memories"])
    assert all("caching" not in m["text"] for m in res["memories"])


def test_search_empty_query(store):
    _add(store, "something")
    assert store.search("") == {"memories": [], "symbols": []}


def test_duplicate_detection_and_merge(store):
    a = _add(store, "Auth uses JWT tokens in an httpOnly cookie for sessions", kind="decision")
    b = _add(store, "Auth uses JWT tokens in httpOnly cookie for the sessions", kind="gotcha")
    _add(store, "completely unrelated fact about kafka partitions")
    groups = store.duplicate_groups()
    assert len(groups) == 1 and len(groups[0]) == 2
    archived = store.merge_memories(a.id, [b.id])
    assert archived == 1
    assert store.get_memory(b.id)["status"] == "abandoned"
    assert len(store.memories(status="active")) == 2  # kept + unrelated


def test_reanchor_clears_stale(store):
    m = _add(store, "anchored fact")
    store.set_stale(m.id, True, 123.0, ["svc.py"])
    assert store.get_memory(m.id)["stale"]
    assert store.reanchor_memory(m.id, "deadbeef1234")
    got = store.get_memory(m.id)
    assert not got["stale"] and got["commit_range"] == "deadbeef1234"


def test_flush_scopes(store, repo):
    from omni_memory import branch as branchmod
    _add(store, "a memory")
    branchmod.full_refresh(store, repo)              # builds code graph + branches
    assert store.has_code_graph()
    store.flush("graph")
    assert not store.has_code_graph()
    assert len(store.memories(status="active")) == 1  # memory survived a graph flush
    store.flush("all")
    assert len(store.memories(status="active")) == 0


def test_overview_shape(store, repo):
    from omni_memory import branch as branchmod
    _add(store, "d1", kind="decision")
    _add(store, "g1", kind="gotcha")
    branchmod.full_refresh(store, repo)
    ov = store.overview()
    assert ov["memories"]["active"] == 2
    assert ov["kinds"].get("decision") == 1
    assert ov["code"]["symbols"] > 0
    assert ov["branches"]["total"] >= 1


def test_export_import_roundtrip(store, tmp_path):
    from omni_memory.store import Store
    _add(store, "Auth uses JWT in an httpOnly cookie", kind="decision", files=["a.py"])
    _add(store, "never charge before the order row commits", kind="gotcha")
    data = store.export_memories()
    assert len(data["memories"]) == 2
    # a second, separate store imports it
    other = Store(exact_dir=tmp_path / "other-store")
    assert other.import_memories(data) == 2
    assert other.import_memories(data) == 0            # idempotent (ids skipped)
    src_ids = {m["id"] for m in store.memories(status="active")}
    dst_ids = {m["id"] for m in other.memories(status="active")}
    assert src_ids == dst_ids                          # ids preserved → citations survive


def test_committed_memory_bootstraps_on_fresh_clone(store, repo):
    import json
    from omni_memory import cli
    (repo / "omni-memory.json").write_text(json.dumps({
        "omni_memory_export": 1,
        "memories": [{"id": "aaa111bbb222", "kind": "decision",
                      "text": "Auth uses JWT in httpOnly cookie", "branch": "main"}]}))
    assert len(store.memories(status="active")) == 0
    cli._bootstrap_shared(store, repo)                 # fresh clone → auto-load
    assert len(store.memories(status="active")) == 1
    cli._bootstrap_shared(store, repo)                 # store non-empty → skip
    assert len(store.memories(status="active")) == 1


def test_atomic_write_concurrent(tmp_path):
    import threading
    from omni_memory.store import atomic_write
    f = tmp_path / "AGENTS.md"
    errs = []

    def w(n):
        for _ in range(40):
            try:
                atomic_write(f, f"content-{n}-" + "x" * 500)
            except Exception as e:  # noqa: BLE001
                errs.append(e)

    ts = [threading.Thread(target=w, args=(i,)) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not errs                                   # no races within one process
    txt = f.read_text()
    assert txt.startswith("content-") and txt.endswith("x" * 20)  # never half-written
    assert not [p for p in tmp_path.iterdir() if ".tmp." in p.name]  # no leftovers


def test_self_ignore_written(store, repo):
    # opening the store (the `store` fixture) creates .omni-memory/.gitignore = *
    assert (repo / ".omni-memory" / ".gitignore").read_text().strip() == "*"
