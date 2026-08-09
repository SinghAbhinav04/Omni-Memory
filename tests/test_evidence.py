"""Evidence tier: verified (by outcome) vs stated vs inferred — drives trust,
ranking, injection markers, and how aggressively memory is pruned."""
from omni_memory import inject, rank
from omni_memory.eviction import evict_score
from omni_memory.store import Memory


def _add(store, text, evidence="stated", kind="fact"):
    return store.add_memory(Memory(text=text, kind=kind, branch="main", evidence=evidence))


def test_evidence_persists_and_defaults(store):
    a = _add(store, "confirmed by a failing test", evidence="verified")
    b = _add(store, "just a note")                      # default
    assert store.get_memory(a.id)["evidence"] == "verified"
    assert store.get_memory(b.id)["evidence"] == "stated"


def test_update_evidence(store):
    m = _add(store, "was a guess", evidence="inferred")
    store.update_memory(m.id, evidence="verified")
    assert store.get_memory(m.id)["evidence"] == "verified"
    store.update_memory(m.id, evidence="bogus")         # invalid ignored
    assert store.get_memory(m.id)["evidence"] == "verified"


def test_inject_marks_evidence(store, repo):
    _add(store, "double-charge bug: commit order row before charge()", evidence="verified", kind="gotcha")
    _add(store, "retry handler probably uses backoff", evidence="inferred", kind="assumption")
    block = inject.build_block(store, repo, query="")
    assert "✓" in block and "~" in block
    assert "verified by outcome" in block               # the legend


def test_verified_outranks_inferred(store):
    _add(store, "auth uses JWT in a cookie", evidence="inferred", kind="decision")
    _add(store, "auth uses JWT in a cookie", evidence="verified", kind="decision")
    ranked = rank.rank(store.memories(status="active"), "auth jwt cookie")
    assert ranked[0]["evidence"] == "verified"


def test_eviction_asymmetry():
    now = 1_000_000.0
    base = {"kind": "fact", "confidence": 0.8, "uses": 0, "stale": 1,
            "stale_since": now - 200 * 86400}
    inferred = evict_score({**base, "evidence": "inferred"}, "active", now)
    stated = evict_score({**base, "evidence": "stated"}, "active", now)
    verified = evict_score({**base, "evidence": "verified"}, "active", now)
    assert inferred > stated > verified                 # guesses pruned first, outcome-proven last


def test_export_import_carries_evidence(store, tmp_path):
    from omni_memory.store import Store
    _add(store, "outcome-proven fact", evidence="verified")
    other = Store(exact_dir=tmp_path / "s2")
    other.import_memories(store.export_memories())
    assert any(m["evidence"] == "verified" for m in other.memories(status="active"))
