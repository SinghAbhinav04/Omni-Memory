"""Temporal-provenance conformance suite.

The six failure cases four independent implementations arrived at from different
directions, frozen as vendor-neutral fixtures. The shared invariant underneath them:

    historical evidence may remain valid without being admissible evidence for the
    current session, the current world-state, or the current use.

Every case is paired with a non-failure control, because agreement is cheap without
one: a detector that never fires and a detector that always fires both look like
agreement from the outside. Where a case can be got wrong in a specific, tempting way
(collapsing two verdicts that share a symptom), the control asserts the wrong answer is
NOT produced rather than merely that the right one is.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from omni_memory import (collector, gitmeta, session_memory as sm, staleness,
                         systemmap, witness)
from omni_memory.graph import build as codegraph
from omni_memory.store import Memory


def _transcript(path, root, n, tool="Read", fp="svc.py"):
    path.write_text("\n".join(json.dumps({"sessionId": "s", "message": {"content": [
        {"type": "tool_use", "name": tool, "input": {"file_path": str(root / fp)}}]}})
        for _ in range(n)), encoding="utf-8")
    return path


# ══ CASE 1 · same locator, different observed bytes ═════════════════════════

def test_case1_source_moved_between_read_and_capture_is_unbound(store, repo):
    """The locator resolves perfectly — to the wrong observation. Worse than an absence,
    because it reads as confidence."""
    store.read_ledger_put("svc.py", "a" * 40)          # what the agent READ
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")  # bytes differ at capture
    assert store.get_memory(m.id)["unbound"] is True


def test_case1_control_honest_capture_is_not_unbound(store, repo):
    """Control: if any capture read UNBOUND the flag would carry no information."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")
    d = store.get_memory(m.id)
    assert d["unbound"] is False and d["observed"] is True


# ══ CASE 2 · same locator and digest, different session ════════════════════

def test_case2_a_prior_sessions_read_cannot_bind_this_capture(store, repo):
    """A day-old observation standing in as proof that the agent saw these bytes NOW.
    Same false-observed class as hashing at write time, one axis over."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    store.clear_read_ledger()                          # SessionStart boundary
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")
    assert store.get_memory(m.id)["observed"] is False  # declared, not observed


def test_case2_control_same_file_same_digest_own_session_is_observed(store, repo):
    """Control: the session filter must not reject a legitimate observation, or
    `observed` would be unreachable and the metric a constant zero."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")
    assert store.get_memory(m.id)["observed"] is True


# ══ CASE 3 · same capture, later source drift ══════════════════════════════

def test_case3_a_later_edit_is_drift_and_must_not_read_unbound(store, repo):
    """The tempting collapse: both questions produce "digest mismatch", so a naive
    implementation compares once and branches. They are different comparisons against
    different references — captured-vs-observed and observed-vs-current — and a genuine
    later edit is the input that catches an implementation which conflated them."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")
    assert store.get_memory(m.id)["unbound"] is False   # honest at capture

    (repo / "svc.py").write_text("# rewritten after capture\n")
    rec = staleness.reconcile(store, repo)
    assert rec["drifted"] == 1
    assert store.get_memory(m.id)["unbound"] is False   # still NOT unbound — it drifted


def test_case3_control_an_untouched_source_stays_fresh(store, repo):
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    sm.remember(store, repo, "claim about svc", kind="fact", files=["svc.py"], source="manual")
    rec = staleness.reconcile(store, repo)
    assert rec["fresh"] == 1 and rec["drifted"] == 0


def test_case3_a_record_can_be_both_unbound_and_drifted(store, repo):
    """Neither axis may pre-empt the other: "did the bytes match what I observed" and
    "did the observation sit on a source that has since moved" are two questions."""
    store.read_ledger_put("svc.py", "b" * 40)          # observation ≠ capture → unbound
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")
    (repo / "svc.py").write_text("# and then it moved again\n")
    rec = staleness.reconcile(store, repo)
    assert store.get_memory(m.id)["unbound"] is True    # axis 1 still exposed
    assert rec["drifted"] == 1                          # axis 2 measured independently


# ══ CASE 4 · same verified state, changed before use ═══════════════════════

def test_case4_a_source_that_moves_between_pull_and_use_is_stale_at_use(store, repo):
    """Verified at pull, acted on later. The check that would catch it is the one nobody
    thinks to re-run — so the verification is carried forward instead."""
    m = sm.remember(store, repo, "svc claim", kind="fact", files=["svc.py"], source="manual")
    witness.pin(store, repo, [store.get_memory(m.id)])
    (repo / "svc.py").write_text("# moved mid-task\n")
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] and len(r["stale_at_use"]) == 1


def test_case4_control_a_steady_source_must_not_cry_wolf(store, repo):
    m = sm.remember(store, repo, "svc claim", kind="fact", files=["svc.py"], source="manual")
    witness.pin(store, repo, [store.get_memory(m.id)])
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] and r["stale_at_use"] == []


def test_case4_a_witness_that_bound_nothing_is_not_clean(store, repo):
    """The honesty rule inside case 4: `if not mismatches: return ok` cannot tell "I
    checked and found nothing" from "I checked nothing"."""
    m = sm.remember(store, repo, "claim on a ghost", kind="fact",
                    files=["ghost.py"], source="manual")
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] is False and "NOT checked" in r["summary"]


# ══ CASE 5 · collector stops, coverage unchanged ═══════════════════════════

def test_case5_a_dead_collector_is_detected_from_outside_the_store(store, repo):
    """Any provenance resting on an out-of-band collector needs a liveness check on that
    collector, and the check cannot live inside the thing it checks."""
    t = _transcript(repo / "t.jsonl", repo, 5)
    assert collector.liveness(store, repo, transcript=t)["verdict"] == "FAIL"


def test_case5_control_a_live_collector_reads_ok(store, repo):
    store.read_ledger_put("svc.py", "a" * 40)
    t = _transcript(repo / "t.jsonl", repo, 5)
    assert collector.liveness(store, repo, transcript=t)["verdict"] == "OK"


def test_case5_an_idle_session_cannot_certify_a_dead_collector(store, repo):
    """The state pair this case exists to separate: an empty ledger because nothing was
    read, versus an empty ledger because the collector was dead. Identical from inside
    the data; only the external witness tells them apart."""
    idle = collector.liveness(store, repo, transcript=_transcript(repo / "a.jsonl", repo, 0))
    dead = collector.liveness(store, repo, transcript=_transcript(repo / "b.jsonl", repo, 3))
    assert idle["verdict"] == "SKIP" and dead["verdict"] == "FAIL"
    assert idle["verdict"] != "OK"                      # absence is never a pass


# ══ CASE 6 · writer/query identifier mismatch ══════════════════════════════

def test_case6_a_key_written_one_way_and_queried_another_loses_the_binding(store, repo):
    """Sounds trivial; silently degrades every observation to declared when it happens,
    and passes any test that only checks whether a verdict was produced. The read hook
    writes a repo-relative path, so a memory anchored with a differently-spelled path
    finds nothing — and must then refuse to claim `observed`."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))   # writer's spelling
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["./svc.py"], source="manual")                 # query's spelling
    assert store.get_memory(m.id)["observed"] is False   # refuses, rather than pretending


def test_case6_control_matching_spellings_do_bind(store, repo):
    """Control: if the lookup could never succeed, the test above would pass for the
    wrong reason and `observed` would be dead code."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    m = sm.remember(store, repo, "claim about svc", kind="fact",
                    files=["svc.py"], source="manual")
    assert store.get_memory(m.id)["observed"] is True


# ══ the two-numbers rule, applied to reconcile()'s coverages ═══════════════

def test_reconcile_coverages_are_forced_apart(store, repo):
    """`reconcile` returns four figures a reader could confuse. In the happy path
    locator_coverage == refetch_coverage, so a mutant swapping them survives. This is
    the input that separates them: two memories carry content keys (locator 100%) but
    only one still resolves fresh (refetch 50%)."""
    (repo / "other.py").write_text("def helper():\n    return 2\n")
    sm.remember(store, repo, "fresh claim", kind="fact", files=["svc.py"], source="manual")
    sm.remember(store, repo, "will drift", kind="fact", files=["other.py"], source="manual")
    (repo / "other.py").write_text("# drifted\n")
    rec = staleness.reconcile(store, repo)
    assert rec["locator_coverage"] == 1.0
    assert rec["refetch_coverage"] == 0.5
    assert rec["locator_coverage"] != rec["refetch_coverage"]


def test_reconcile_denominator_excludes_what_can_never_be_bound(store, repo):
    """not-bindable sits BESIDE the denominator, never inside — otherwise coverage can
    never reach 100% and a team chases it forever, or quietly redefines it."""
    sm.remember(store, repo, "bindable", kind="fact", files=["svc.py"], source="manual")
    store.add_memory(Memory(text="no anchor at all", kind="decision", branch="main"))
    rec = staleness.reconcile(store, repo)
    assert rec["anchored"] == 1 and rec["not_bindable"] == 1
    assert rec["refetch_coverage"] == 1.0               # not dragged down by the un-bindable


# ══ the mirror-invariant class ═════════════════════════════════════════════
# An invariant that pushes both sides of its comparison through the same transform is
# true by construction: the antecedent is reached, the assertion runs, and it passes on
# every store — including one where writer and reader genuinely disagree. Coverage does
# not touch it, so it needs its own detector.

_MIRROR = re.compile(r"\b(\w+)\s*\(([^()]+)\)\s*==\s*\1\s*\(")


def _scan_mirrors(text: str) -> list[str]:
    return [m.group(0) for m in _MIRROR.finditer(text)]


def test_the_mirror_detector_fires_on_a_planted_mirror():
    """A zero from a detector that cannot fire is a silence, not a measurement — so the
    detector is proved against a known-positive before its zero is believed."""
    planted = "assert norm(a) == norm(b)"
    assert _scan_mirrors(planted)
    assert not _scan_mirrors("assert norm(a) == b")      # and does not over-fire


def test_no_mirror_invariants_in_the_provenance_modules():
    """Now the measurement, which means something because of the test above."""
    root = Path(__file__).resolve().parent.parent / "omni_memory"
    hits = []
    for f in ("witness.py", "collector.py", "identifier.py", "staleness.py", "systemmap.py"):
        for h in _scan_mirrors((root / f).read_text(encoding="utf-8")):
            hits.append(f"{f}: {h}")
    assert not hits, "comparison with both sides through one transform: " + "; ".join(hits)


# ══ fixture integrity ══════════════════════════════════════════════════════

def test_the_sample_repo_carries_what_these_tests_assume(store, repo):
    """A test asserts the integrity of its own fixture before it asserts anything about
    behaviour. A silently-empty fixture reaches every assertion below carrying nothing
    and passes — which is the escape-hatch class relocated into the assertion."""
    assert (repo / "svc.py").is_file()
    assert gitmeta.is_repo(repo)
    assert gitmeta.blob_sha(repo, "svc.py")             # hashable, so digests are real
    codegraph.build_code_graph(store, repo)
    nodes, _ = store.code_graph()
    assert any(n["name"] == "create_order" for n in nodes)  # the symbol tests anchor on
