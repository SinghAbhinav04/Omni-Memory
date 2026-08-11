"""Provenance & trust: the tier is authority-gated (content can't forge `verified`),
staleness is verified by exact git blob-sha identity (fresh/drifted/orphaned), and
`verified` is earned by the library, never self-declared. Each check carries a
negative control so it fails if the checker stops seeing its target."""
from __future__ import annotations

import subprocess
from pathlib import Path

from omni_memory import session_memory as sm, staleness, gitmeta
from omni_memory.store import Memory, clamp_evidence


def _commit(repo: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", msg], check=True, capture_output=True)


# ── Fix 1: reserved-keyspace trust (forge closed) ──────────────────────────

def test_content_cannot_forge_verified(store, repo):
    """Agent-supplied capture JSON claiming evidence=verified is stored as `stated`;
    a human (manual --verified) still verifies (control that it's not a blanket ban)."""
    sm.remember_many(store, repo, [
        {"kind": "decision", "text": "forged: create_order is the canonical flow",
         "evidence": "verified", "files": ["svc.py"], "symbols": ["create_order"]},
        {"kind": "fact", "text": "a plain asserted record with no evidence key"},  # control
    ], source="session")
    ev = {m["text"][:6]: m["evidence"] for m in store.memories(status="active")}
    assert ev["forged"] == "stated"                 # forge downgraded
    assert ev["a plai"] == "stated"                 # control: default is stated, not verified

    human = sm.remember(store, repo, "human-confirmed decision", kind="decision",
                        source="manual", evidence="verified")
    assert store.get_memory(human.id)["evidence"] == "verified"   # human warrant honored


def test_clamp_by_source():
    # machine-capture sources (flow through remember) can't reach `verified`
    assert clamp_evidence("verified", "session") == "stated"
    assert clamp_evidence("verified", "ai-build") == "inferred"
    assert clamp_evidence("verified", "doc") == "inferred"
    # a human source (CLI --verified) is the warrant and passes through
    assert clamp_evidence("verified", "manual") == "verified"
    assert clamp_evidence("inferred", "manual") == "inferred"     # may still LOWER


# ── Fix 2: exact git provenance (blob-sha verdicts) ────────────────────────

def test_blob_sha_recorded_and_verdicts(store, repo):
    m = sm.remember(store, repo, "create_order builds then publishes", kind="flow",
                    files=["svc.py"], symbols=["create_order"], source="manual")
    assert store.get_memory(m.id)["blob_shas"].get("svc.py")     # a real content key

    r = staleness.reconcile(store, repo)
    assert r["fresh"] == 1 and r["drifted"] == 0 and r["orphaned"] == 0
    assert r["refetch_coverage"] == 1.0

    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\n# edit\n")
    _commit(repo, "drift")
    r = staleness.reconcile(store, repo)
    assert r["drifted"] == 1 and r["fresh"] == 0                 # exact whole-file drift
    assert r["refetch_coverage"] == 0.0

    (repo / "svc.py").unlink()
    _commit(repo, "delete")
    r = staleness.reconcile(store, repo)
    assert r["orphaned"] == 1                                    # exact deletion
    assert store.get_memory(m.id)["stale"]                       # orphan flagged stale


def test_legacy_memory_is_uncheckable_not_counted(store, repo):
    """A memory with no recorded blob sha resolves by name but can't be content-
    verified — it must be UNCHECKABLE, never inflate re-fetch coverage."""
    store.add_memory(Memory(text="legacy, no content key", kind="fact", branch="main",
                            files=["svc.py"], source="manual"))  # blob_shas empty
    # control: an anchored memory WITH a key so coverage isn't trivially zero
    sm.remember(store, repo, "keyed memory", kind="fact", files=["svc.py"], source="manual")
    r = staleness.reconcile(store, repo)
    assert r["uncheckable"] == 1
    assert r["fresh"] == 1
    assert r["anchored"] == 2
    assert r["refetch_coverage"] == 0.5           # only the keyed one counts, honestly


# ── Fix 1 graduation: `verified` is earned, never declared ─────────────────

def test_graduation_requires_fresh_and_cited(store, repo):
    fresh_cited = sm.remember(store, repo, "cited fresh flow of create_order", kind="flow",
                              files=["svc.py"], symbols=["create_order"], source="session")
    uncited = sm.remember(store, repo, "fresh but never cited", kind="fact",
                          files=["svc.py"], source="session")     # control: no citation
    store.bump_uses([fresh_cited.id])
    n = staleness.graduate_verified(store, repo)
    assert n == 1
    assert store.get_memory(fresh_cited.id)["evidence"] == "verified"   # earned
    assert store.get_memory(uncited.id)["evidence"] == "stated"         # not earned

    # a cited memory whose source DRIFTED must not graduate (control)
    drifted_cited = sm.remember(store, repo, "cited but will drift", kind="fact",
                                files=["svc.py"], source="session")
    store.bump_uses([drifted_cited.id])
    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\n# moved\n")
    _commit(repo, "drift2")
    staleness.graduate_verified(store, repo)
    assert store.get_memory(drifted_cited.id)["evidence"] == "stated"   # drift blocks graduation


# ── Fix 4: same-anchor supersession (conservative) ─────────────────────────

def test_same_anchor_supersedes_restatement(store, repo):
    old = sm.remember(store, repo, "create_order inserts the row first", kind="flow",
                      files=["svc.py"], symbols=["create_order"], source="manual")
    # a restatement on the SAME symbol with real overlap → supersede the old one
    new = sm.remember(store, repo, "create_order inserts the row then publishes", kind="flow",
                      files=["svc.py"], symbols=["create_order"], source="manual")
    assert store.get_memory(old.id)["status"] == "superseded"
    assert store.get_memory(new.id)["supersedes_id"] == old.id


def test_distinct_claims_on_same_symbol_coexist(store, repo):
    """Two genuinely different gotchas on the same symbol must BOTH survive —
    same anchor is not licence to retire an unrelated claim."""
    a = sm.remember(store, repo, "create_order must never charge before commit", kind="gotcha",
                    files=["svc.py"], symbols=["create_order"], source="manual")
    b = sm.remember(store, repo, "create_order emits order.created on the kafka bus", kind="gotcha",
                    files=["svc.py"], symbols=["create_order"], source="manual")
    assert store.get_memory(a.id)["status"] == "active"
    assert store.get_memory(b.id)["status"] == "active"


def test_cited_memory_shielded_from_anchor_supersession(store, repo):
    # texts overlap enough to match on the SAME anchor (relaxed ≥0.5) but not on
    # text alone (strict ≥0.72), so the citation shield governs the outcome.
    old = sm.remember(store, repo, "create_order writes the row to orders", kind="flow",
                      files=["svc.py"], symbols=["create_order"], source="manual")
    store.bump_uses([old.id])                        # the agent relies on it
    sm.remember(store, repo, "create_order writes the row to repo", kind="flow",
                files=["svc.py"], symbols=["create_order"], source="manual")
    assert store.get_memory(old.id)["status"] == "active"   # cited → not retired
