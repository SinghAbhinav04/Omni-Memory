"""Observation binding — a separate axis from refetchability. Provenance must bind
to the bytes the agent actually READ (via the read-ledger), not the file at capture;
a source that moved between read and capture is UNBOUND_CAPTURE. Paired negative
controls: an honest capture reads FRESH + observed, a genuine later edit reads DRIFT,
and a store with nothing ledger-backed refuses to report `observed`."""
from __future__ import annotations

import subprocess
from pathlib import Path

from omni_memory import session_memory as sm, staleness, gitmeta
from omni_memory.store import Store


def _commit(repo: Path, msg: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", msg], check=True, capture_output=True)


def test_binds_to_read_ledger_and_is_observed(store, repo):
    """Control: an honest capture — the agent read exactly the bytes present at
    capture — is `observed` and FRESH, not merely declared."""
    (repo / "svc.py").write_text("def create_order():\n    return 'A'\n")
    _commit(repo, "A")
    read_digest = gitmeta.blob_sha(repo, "svc.py")
    store.read_ledger_put("svc.py", read_digest)              # agent read these bytes
    m = sm.remember(store, repo, "create_order returns A", kind="fact",
                    files=["svc.py"], source="manual")
    got = store.get_memory(m.id)
    assert got["observed"] is True and got["unbound"] is False
    assert got["blob_shas"]["svc.py"] == read_digest
    rec = staleness.reconcile(store, repo)
    assert rec["fresh"] >= 1 and rec["observation_binding_coverage"] == 1.0 and rec["unbound"] == 0


def test_unbound_capture_when_source_moves_between_read_and_capture(store, repo):
    """The bug DanceNitra generalized: agent reads value A, the file becomes B before
    capture. Provenance must bind to A (what was read) and flag UNBOUND_CAPTURE — the
    remedy is re-read + re-capture, distinct from a later DRIFT."""
    (repo / "svc.py").write_text("def create_order():\n    return 'A'\n")
    _commit(repo, "A")
    read_a = gitmeta.blob_sha(repo, "svc.py")
    store.read_ledger_put("svc.py", read_a)                   # agent OBSERVED 'A'
    (repo / "svc.py").write_text("def create_order():\n    return 'B'\n")  # moved before capture
    _commit(repo, "B")
    m = sm.remember(store, repo, "create_order returns A", kind="fact",
                    files=["svc.py"], source="manual")
    got = store.get_memory(m.id)
    assert got["blob_shas"]["svc.py"] == read_a               # bound to what was READ (A), not B
    assert got["unbound"] is True                             # UNBOUND_CAPTURE
    rec = staleness.reconcile(store, repo)
    assert rec["unbound"] >= 1


def test_declared_when_no_read_record(store, repo):
    """Control: no read-ledger entry → the memory is `declared` (capture-time), not
    `observed`. The library cannot claim to know what it didn't see."""
    (repo / "svc.py").write_text("def create_order():\n    return 1\n")
    _commit(repo, "x")
    m = sm.remember(store, repo, "create_order returns 1", kind="fact",
                    files=["svc.py"], source="manual")            # no read_ledger_put
    assert store.get_memory(m.id)["observed"] is False
    rec = staleness.reconcile(store, repo)
    assert rec["observation_binding_coverage"] == 0.0             # refuses to report observed


def test_observed_memory_drifts_on_later_edit(store, repo):
    """Control: a genuine edit AFTER an observed capture must read DRIFTED (observed
    vs now), independent of the unbound question."""
    (repo / "svc.py").write_text("def create_order():\n    return 'A'\n")
    _commit(repo, "A")
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    sm.remember(store, repo, "create_order returns A", kind="fact",
                files=["svc.py"], source="manual")
    (repo / "svc.py").write_text("def create_order():\n    return 'C'\n")  # later edit
    _commit(repo, "C")
    rec = staleness.reconcile(store, repo)
    assert rec["drifted"] >= 1
