"""Identifier contract: declared once, re-measured against the store's own keys.

Two disciplines from the provenance thread are enforced here rather than described:

  · where a function returns two numbers a reader could confuse, no fixture may leave
    them equal — otherwise a mutant swapping them survives the whole suite;
  · a zero has two causes, and only one is good news, so it is never reported bare.
"""
from __future__ import annotations

import json

import pytest

from omni_memory import identifier, session_memory as sm, staleness, team
from omni_memory.graph import build as codegraph


# ── the two numbers must be kept apart ──────────────────────────────────────

def test_pairwise_merges_leave_the_two_numbers_equal():
    """The sleepy fixture, asserted as sleepy. Two separate 2-key merges give
    groups_colliding == keys_lost == 2, so a swap-mutant survives THIS input. It is kept
    deliberately, and named, so that if a later change makes them diverge here someone
    is told the fixture stopped being the undiscriminating one."""
    cost = identifier.fold_cost(["aa1", "aa2", "bb1", "bb2"], lambda k: k[:2])
    assert cost["groups_colliding"] == 2 and cost["keys_lost"] == 2


def test_a_three_way_merge_forces_the_two_numbers_apart():
    """One bucket holding three keys loses TWO keys — this is the input that kills a
    mutant returning the count of colliding groups where keys lost was meant."""
    cost = identifier.fold_cost(["aa1", "aa2", "aa3", "bb1"], lambda k: k[:2])
    assert cost["groups_colliding"] == 1
    assert cost["keys_lost"] == 2
    assert cost["groups_colliding"] != cost["keys_lost"]


def test_mixed_merge_widths_keep_them_apart():
    cost = identifier.fold_cost(
        ["aa1", "aa2", "bb1", "bb2", "bb3", "cc1", "cc2", "cc3", "cc4", "dd1"],
        lambda k: k[:2])
    assert (cost["groups_colliding"], cost["keys_lost"]) == (3, 6)


def test_no_collision_reports_zero_for_both():
    cost = identifier.fold_cost(["ab", "cd", "ef"], lambda k: k[:2])
    assert cost["groups_colliding"] == 0 and cost["keys_lost"] == 0
    assert cost["buckets"] == 3


# ── a zero is reported three-valued, never as a clean bill of health ────────

def test_zero_on_a_small_population_is_not_yet_measurable(store):
    """13 uuid-derived keys against a 48-bit fold collide with vanishing probability
    whatever the fold is. Calling that 'safe' would be reading the absence of a signal
    as a property of the contract."""
    from omni_memory.store import Memory
    for i in range(13):
        store.add_memory(Memory(text=f"m{i}", kind="fact", branch="main"))
    c = identifier.identifier_contract(store)
    assert c["cost"]["keys_lost"] == 0
    assert c["verdict"] == "NOT_YET_MEASURABLE"
    assert c["keys"] < c["threshold"]
    assert "NOT a property of the fold" in c["why"]


def test_a_measured_collision_outranks_the_model():
    """If keys demonstrably merge, no threshold argument makes that fine — the
    measurement wins over the model, always."""
    keys = ["aaaa1", "aaaa2", "aaaa3"]
    cost = identifier.fold_cost(keys, lambda k: k[:4])
    space = identifier.measured_space(keys, 4)
    v = identifier.fold_verdict(cost, space, identifier.population_threshold(space["space"]))
    assert v["verdict"] == "COST_MEASURED"


def test_saturated_positions_block_the_at_scale_verdict():
    """A plug-in entropy from n samples cannot see an alphabet wider than n. When every
    position is saturated the space estimate IS the sample size in disguise, so 'zero at
    scale' would be an unearned claim."""
    keys = ["ab", "cd", "ef"]                      # 3 samples, 3 distinct chars/position
    space = identifier.measured_space(keys, 2)
    assert space["positions_saturated"] > 0
    cost = identifier.fold_cost(keys, lambda k: k[:2])
    v = identifier.fold_verdict(cost, space, 1)    # threshold cleared on purpose
    assert v["verdict"] == "NOT_YET_MEASURABLE"


# ── headroom: the companion that needs no statistical model ─────────────────

def test_headroom_reports_how_far_the_fold_could_shrink():
    """Answers a different question from the population threshold — 'how much slack'
    rather than 'how many more keys'."""
    h = identifier.headroom(["abcd11", "abcd22", "zzzz33"], length=6)
    assert h["collides_at_length"] == 4            # 'abcd' merges two of them
    assert h["headroom_chars"] == 2


def test_structured_keys_have_almost_no_headroom_despite_a_huge_population():
    """The disagreement case: keys sharing a long prefix sit one character from
    collapsing while any population-based reading calls them fine."""
    keys = [f"src/components/ui/x{i}" for i in range(200)]
    h = identifier.headroom(keys, length=len("src/components/ui/x") + 3)
    assert h["headroom_chars"] <= 3                # a hair from merging all 200


# ── form: foreign ids arrive through team shards, not through our writer ───

def test_foreign_ids_from_a_team_shard_are_caught_as_off_form(store, repo):
    """`import_memories` preserves a teammate's id verbatim, so local correctness cannot
    prevent an off-form id. The contract must notice."""
    store.import_memories({"memories": [
        {"id": "LEGACY-1", "text": "from an older omni-memory", "kind": "fact"},
        {"id": "a" * 12, "text": "well-formed", "kind": "fact"},
    ]}, source="shared")
    c = identifier.identifier_contract(store)
    assert c["off_form_count"] == 1
    assert "LEGACY-1" in c["off_form"]


def test_control_locally_minted_ids_are_all_on_form(store, repo):
    """Control for the test above — if our own writer produced off-form ids the check
    would fire constantly and be ignored."""
    for i in range(6):
        sm.remember(store, repo, f"note {i}", kind="fact", files=["svc.py"], source="manual")
    assert identifier.identifier_contract(store)["off_form_count"] == 0


# ── the team.py contradiction: shared memory can re-earn `verified` ─────────

def test_shared_memory_can_graduate_once_cited_and_still_fresh(store, repo):
    """A blob sha is a content hash, identical across clones, so a teammate's anchor
    re-resolves against YOUR checkout. Combined with a local citation, that is the same
    warrant own-capture memory graduates on — which is what team.py has always
    documented."""
    codegraph.build_code_graph(store, repo)
    from omni_memory import gitmeta
    shas = gitmeta.blob_shas(repo, ["svc.py"])
    store.import_memories({"memories": [{
        "id": "b" * 12, "text": "create_order publishes after insert", "kind": "flow",
        "files": ["svc.py"], "symbols": ["create_order"], "blob_shas": shas,
        "evidence": "stated", "branch": "main"}]}, source="shared")
    store.bump_uses(["b" * 12])                    # the agent cited it and it held up
    assert staleness.graduate_verified(store, repo) == 1
    assert store.get_memory("b" * 12)["evidence"] == "verified"


def test_control_shared_memory_on_a_drifted_source_must_not_graduate(store, repo):
    """The control that makes the test above mean something: graduation is gated on
    reality, not on origin. Move the source and the same memory must stay `stated`."""
    codegraph.build_code_graph(store, repo)
    store.import_memories({"memories": [{
        "id": "c" * 12, "text": "stale claim about svc", "kind": "flow",
        "files": ["svc.py"], "blob_shas": {"svc.py": "d" * 40},   # never matched HEAD
        "evidence": "stated", "branch": "main"}]}, source="shared")
    store.bump_uses(["c" * 12])
    assert staleness.graduate_verified(store, repo) == 0
    assert store.get_memory("c" * 12)["evidence"] == "stated"


def test_uncited_shared_memory_does_not_graduate_on_freshness_alone(store, repo):
    """Freshness is necessary, not sufficient — without a local citation there is no
    outcome to warrant the top tier."""
    codegraph.build_code_graph(store, repo)
    from omni_memory import gitmeta
    store.import_memories({"memories": [{
        "id": "e" * 12, "text": "never cited", "kind": "flow", "files": ["svc.py"],
        "blob_shas": gitmeta.blob_shas(repo, ["svc.py"]),
        "evidence": "stated", "branch": "main"}]}, source="shared")
    assert staleness.graduate_verified(store, repo) == 0


def test_graduated_shared_memory_still_renders_as_external(store, repo):
    """Graduation raises the evidence tier; it must NOT launder the origin. The agent
    still has to be told this came from outside its own capture."""
    codegraph.build_code_graph(store, repo)
    from omni_memory import gitmeta, inject
    store.import_memories({"memories": [{
        "id": "f" * 12, "text": "teammate note about create_order", "kind": "flow",
        "files": ["svc.py"], "blob_shas": gitmeta.blob_shas(repo, ["svc.py"]),
        "evidence": "stated", "branch": "main"}]}, source="shared")
    store.bump_uses(["f" * 12])
    staleness.graduate_verified(store, repo)
    block = inject.build_block(store, repo, query="create_order")
    assert "↗external" in block                    # provenance survives the promotion
