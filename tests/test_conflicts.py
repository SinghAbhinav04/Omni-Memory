"""Merge reconciliation: when a branch merges, duplicate memories collapse and
contradictory ones on the same symbol are flagged as conflicts to resolve."""
from __future__ import annotations

from omni_memory import inject
from omni_memory.store import Memory


def _mem(store, branch, kind, text, symbols, cited=False, source="manual"):
    m = store.add_memory(Memory(text=text, kind=kind, branch=branch,
                                files=["auth.py"], symbols=symbols, source=source))
    if cited:
        store.bump_uses([m.id])
    return m


# ── conflict on merge ──────────────────────────────────────────────────────

def test_contradiction_on_merge_is_flagged(store, repo):
    base = _mem(store, "main", "decision", "auth uses session-based auth", ["authenticate"])
    feat = _mem(store, "feature/auth", "decision", "auth uses JWT with refresh tokens", ["authenticate"])
    rec = store.reanchor_branch("feature/auth", "main")
    assert rec["conflicts"] == 1 and rec["deduped"] == 0
    assert store.conflict_member_ids() == {base.id, feat.id}
    assert store.get_memory(feat.id)["branch"] == "main"          # moved onto base
    # both still active (nothing lost) and both flagged in the inject block
    block = inject.build_block(store, repo, query="auth authenticate")
    assert "⚠CONFLICT" in block


def test_resolve_keep_supersedes_the_other(store, repo):
    base = _mem(store, "main", "decision", "auth uses session-based auth", ["authenticate"])
    feat = _mem(store, "feature/auth", "decision", "auth uses JWT with refresh tokens", ["authenticate"])
    store.reanchor_branch("feature/auth", "main")
    n = store.resolve_conflict(feat.id, keep=True)                # JWT wins
    assert n == 1
    assert store.get_memory(feat.id)["status"] == "active"
    assert store.get_memory(base.id)["status"] == "superseded"
    assert store.get_memory(feat.id)["supersedes_id"] == base.id  # winner → loser lineage
    assert store.open_conflicts() == []


def test_resolve_both_keeps_both(store, repo):
    base = _mem(store, "main", "decision", "auth uses session-based auth", ["authenticate"])
    feat = _mem(store, "feature/auth", "decision", "auth uses JWT with refresh tokens", ["authenticate"])
    store.reanchor_branch("feature/auth", "main")
    store.resolve_conflict(feat.id, keep=False)                  # both true
    assert store.get_memory(base.id)["status"] == "active"
    assert store.get_memory(feat.id)["status"] == "active"
    assert store.open_conflicts() == []


# ── dedup on merge ─────────────────────────────────────────────────────────

def test_duplicate_on_merge_collapses(store, repo):
    base = _mem(store, "main", "db", "users table has a unique index on email", ["users"])
    _mem(store, "feature/x", "db", "users table has a unique index on email", ["users"])  # identical
    rec = store.reanchor_branch("feature/x", "main")
    assert rec["deduped"] == 1 and rec["conflicts"] == 0
    active = [m for m in store.memories(status="active") if "unique index" in m["text"]]
    assert len(active) == 1                                       # exactly one survives


def test_cited_base_memory_wins_dedup(store, repo):
    base = _mem(store, "main", "db", "orders table is partitioned by month", ["orders"], cited=True)
    feat = _mem(store, "feature/x", "db", "orders table is partitioned by month", ["orders"])
    store.reanchor_branch("feature/x", "main")
    assert store.get_memory(base.id)["status"] == "active"        # cited → shielded, kept
    assert store.get_memory(feat.id)["status"] == "superseded"


# ── no false positives ─────────────────────────────────────────────────────

def test_distinct_gotchas_on_same_symbol_do_not_conflict(store, repo):
    a = _mem(store, "main", "gotcha", "never charge before the create_order row commits", ["create_order"])
    b = _mem(store, "feature/x", "gotcha", "create_order emits order.created on the kafka bus", ["create_order"])
    rec = store.reanchor_branch("feature/x", "main")
    assert rec["conflicts"] == 0 and rec["deduped"] == 0         # gotcha is additive, not a claim
    assert store.get_memory(a.id)["status"] == "active"
    assert store.get_memory(b.id)["status"] == "active"


# ── history ────────────────────────────────────────────────────────────────

def test_history_walks_supersession_chain(store, repo):
    base = _mem(store, "main", "decision", "auth uses session-based auth", ["authenticate"])
    feat = _mem(store, "feature/auth", "decision", "auth uses JWT with refresh tokens", ["authenticate"])
    store.reanchor_branch("feature/auth", "main")
    store.resolve_conflict(feat.id, keep=True)                   # feat supersedes base
    chain = store.history(feat.id)
    ids = [m["id"] for m in chain]
    assert ids == [base.id, feat.id]                            # oldest → newest
    assert chain[-1]["status"] == "active"
