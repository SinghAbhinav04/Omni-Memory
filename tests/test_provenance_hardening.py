"""0.9.27 provenance hardening: the read-ledger can't leak across sessions, memory
that can never be content-bound is reported BESIDE the denominator (not inside), and
the observation collector proves it's actually alive rather than assumed."""
from __future__ import annotations

from omni_memory import session_memory as sm, staleness, cli
from omni_memory.store import Memory


# ── FLAW 1: stale cross-session reads must not falsely bind ─────────────────

def test_session_start_clears_stale_reads(store, repo):
    store.read_ledger_put("svc.py", "a" * 40)             # a read from a PRIOR session
    assert store.read_ledger_get("svc.py")
    store.clear_read_ledger()                             # SessionStart does this
    assert store.read_ledger_get("svc.py") is None
    assert store.read_ledger_count() == 0
    # a capture now — svc.py was NOT read this session → declared, never falsely observed
    m = sm.remember(store, repo, "svc handler note", kind="fact", files=["svc.py"], source="manual")
    assert store.get_memory(m.id)["observed"] is False


# ── FLAW 2 / NOT_BINDABLE: reported beside, never inside the denominator ─────

def test_not_bindable_reported_beside_denominator(store, repo):
    store.add_memory(Memory(text="we use event sourcing across the platform",
                            kind="decision", branch="main"))     # no file / no symbol → not bindable
    sm.remember(store, repo, "svc.py handler builds the row", kind="fact",
                files=["svc.py"], symbols=["create_order"], source="manual")  # bindable
    rec = staleness.reconcile(store, repo)
    assert rec["not_bindable"] == 1
    assert rec["anchored"] == 1                           # not-bindable is NOT in the denominator
    assert rec["observation_binding_coverage"] <= 1.0     # denominator never inflated/deflated by it


# ── FLAW 3 / collector liveness: the read hook must prove it runs ───────────

def test_read_collector_liveness_probe(store, repo, monkeypatch):
    """Runs the read hook the way Claude Code invokes it and confirms it actually
    wrote the ledger — catching the silent-fail class (dead interpreter/import)."""
    monkeypatch.chdir(repo)
    calls = []
    cli._doctor_read_collector(store, repo, lambda ok, label, detail, fix="": calls.append((ok, label)))
    rc = [c for c in calls if c[1] == "read collector"]
    assert rc and rc[0][0] is True                        # collector executes and records
    # the probe must not leave residue that would later falsely bind a memory
    probe = __import__("subprocess").run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout.splitlines()[0]
    assert store.read_ledger_get(probe) is None
