"""Collector liveness, checked against a witness the collector does not own.

The failure this guards is silent and points at confidence: a dead read hook leaves an
empty ledger that is indistinguishable from a quiet session, while every stored record
keeps claiming `observed`. Each case below is paired with the control that stops the
check from being a rubber stamp.
"""
from __future__ import annotations

import json

from omni_memory import collector


def _transcript(path, entries):
    """Write a synthetic runtime transcript. Shaped like the real thing: JSONL whose
    message.content carries tool_use blocks."""
    lines = []
    for name, fp in entries:
        lines.append(json.dumps({"sessionId": "s1", "type": "assistant", "message": {
            "content": [{"type": "tool_use", "name": name, "input": {"file_path": str(fp)}}]}}))
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── the case the whole module exists for ───────────────────────────────────

def test_reads_with_an_empty_ledger_is_a_contradiction_not_a_quiet_session(store, repo):
    t = _transcript(repo / "t.jsonl", [("Read", repo / "svc.py")] * 3)
    lv = collector.liveness(store, repo, transcript=t)
    assert lv["verdict"] == "FAIL"
    assert lv["reads"] == 3 and lv["observations"] == 0
    assert lv["remedy"]                              # a verdict must say what to do


def test_control_reads_with_a_written_ledger_is_healthy(store, repo):
    """Control: if this also read FAIL the check would be noise within a day."""
    store.read_ledger_put("svc.py", "a" * 40)
    t = _transcript(repo / "t.jsonl", [("Read", repo / "svc.py")] * 3)
    assert collector.liveness(store, repo, transcript=t)["verdict"] == "OK"


# ── absence of the witness is never a pass ─────────────────────────────────

def test_no_transcript_skips_rather_than_passing(store, repo):
    """Checking a collector by its own output is the forbidden case, so the absence of
    an external witness must not be reported as evidence of health."""
    lv = collector.liveness(store, repo, transcript=repo / "does-not-exist.jsonl")
    assert lv["verdict"] == "SKIP"
    assert lv["reads"] is None                       # None, not 0 — nothing was measured


def test_a_session_that_touched_nothing_skips_rather_than_passing(store, repo):
    """'No discrepancy' is not 'the collector is alive'. An idle session must not be
    able to certify a dead hook."""
    t = _transcript(repo / "t.jsonl", [])
    lv = collector.liveness(store, repo, transcript=t)
    assert lv["verdict"] == "SKIP" and lv["reads"] == 0
    assert "NOT the same as" in lv["why"]


def test_an_empty_store_and_a_dead_collector_are_told_apart(store, repo):
    """The two states this check exists to separate: same empty ledger, different
    verdicts, decided entirely by the external witness."""
    idle = collector.liveness(store, repo, transcript=_transcript(repo / "a.jsonl", []))
    dead = collector.liveness(store, repo,
                              transcript=_transcript(repo / "b.jsonl", [("Read", repo / "svc.py")]))
    assert idle["verdict"] == "SKIP" and dead["verdict"] == "FAIL"


# ── what counts as an observation ──────────────────────────────────────────

def test_a_bash_touched_file_creates_no_observation_claim(store, repo):
    """A shell command that happened to `cat` a file is not evidence the agent observed
    its contents — it was not read through an instrumented tool. Counting it would
    manufacture the very false-observed the ledger exists to prevent."""
    t = _transcript(repo / "t.jsonl", [("Bash", repo / "svc.py")])
    assert collector.liveness(store, repo, transcript=t)["verdict"] == "SKIP"


def test_files_outside_the_repo_are_not_counted(store, repo, tmp_path_factory):
    """The ledger only records in-repo files, so counting out-of-repo reads would
    produce a FAIL that no amount of a working hook could clear."""
    outside = tmp_path_factory.mktemp("elsewhere") / "other.py"
    outside.write_text("x = 1")
    # fixture integrity first: `repo` IS tmp_path, so a file under tmp_path would be
    # INSIDE the repo and this test would silently assert the opposite of its name.
    assert not str(outside.resolve()).startswith(str(repo.resolve()) + "/")
    t = _transcript(repo / "t.jsonl", [("Read", outside)])
    assert collector.liveness(store, repo, transcript=t)["verdict"] == "SKIP"


def test_edit_and_write_count_as_observations(store, repo):
    t = _transcript(repo / "t.jsonl", [("Edit", repo / "svc.py"), ("Write", repo / "svc.py")])
    assert collector.count_tool_reads(t, repo)["reads"] == 2


def test_repeated_reads_of_one_file_do_not_require_more_ledger_rows(store, repo):
    """The ledger is keyed by path, so re-reading a file adds no row. Comparing the two
    by EQUALITY would fail permanently on a perfectly healthy collector."""
    store.read_ledger_put("svc.py", "a" * 40)
    t = _transcript(repo / "t.jsonl", [("Read", repo / "svc.py")] * 9)
    lv = collector.liveness(store, repo, transcript=t)
    assert lv["verdict"] == "OK"
    assert lv["reads"] == 9 and lv["observations"] == 1   # far apart, and that is correct


def test_malformed_transcript_lines_are_skipped_not_fatal(store, repo):
    """A hook must never break the session; neither may the check on it."""
    t = repo / "t.jsonl"
    t.write_text("not json\n" + json.dumps({"sessionId": "s", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": str(repo / "svc.py")}}]}}))
    assert collector.count_tool_reads(t, repo)["reads"] == 1
