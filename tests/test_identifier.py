"""Identifier contract: declared once, re-measured against the store's own keys.

Two disciplines from the provenance thread are enforced here rather than described:

  · where a function returns two numbers a reader could confuse, no fixture may leave
    them equal — otherwise a mutant swapping them survives the whole suite;
  · a zero has two causes, and only one is good news, so it is never reported bare.
"""
from __future__ import annotations

import hashlib
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


# ── the cliff is found by bisection, and the bisection is not trusted ───────

@pytest.mark.parametrize("name,keys", [
    ("exact duplicates", ["aa", "aa", "bb"]),
    ("single key", ["only"]),
    ("empty", []),
    ("no fold merges", ["a", "b", "c"]),
    ("long shared prefix", [f"src/components/ui/x{i}" for i in range(200)]),
    ("variable length", [("p" * (i % 40 + 1)) + str(i) for i in range(300)]),
    ("fixed-width hex", [f"{i:012x}" for i in range(400)]),
])
def test_bisection_agrees_with_the_linear_reference(name, keys):
    """The predicate is monotone — a prefix that merges two keys keeps merging them as it
    shortens — so the cliff is the last True and a bisection finds it. An optimisation
    that changes the answer is not one, so the reference lives in the module and every
    shape is checked against it, including the degenerate ones a bisection gets wrong."""
    hi = max((len(k) for k in keys), default=1)
    assert identifier.collides_at_length(keys) == (
        identifier.collides_at_length_linear(keys, hi) if len(keys) >= 2 else None)


def test_a_cliff_above_the_declared_fold_is_found():
    """The regression this replaced. The search used to stop at the declared fold, so a
    cliff above it was outside the instrument — and worse, the highest merging length
    BELOW the fold got reported in its place, which reads as far less headroom than the
    truth. Here the true cliff is 20 and the old window (< 12) would have said 11."""
    keys = [f"a_very_long_shared_prefix_{i}" for i in range(50)]
    assert identifier.collides_at_length(keys) > identifier.ID_LENGTH
    assert identifier.collides_at_length(keys) == identifier.collides_at_length_linear(
        keys, max(len(k) for k in keys))


def _hashed(i: int) -> str:
    """Deterministic, unstructured keys. Drawing them at random is how a control ends up
    measuring the draw rather than the metric: a random `ab` + 6-hex population can happen
    to collide a character early, and then the fixture, not the instrument, decides."""
    return hashlib.sha256(str(i).encode()).hexdigest()


def test_planted_cliff_is_detected():
    """400 keys of length 10 sharing `prefix-`, distinguished only by three digits. The
    cliff is one character below the full key — the case the whole argument is about."""
    keys = [f"prefix-{i:03d}" for i in range(400)]
    c = identifier.cliff_block(keys, fold=10)
    assert c["collides_at_length"] == 9
    assert c["distance_to_cliff"] == 1                 # fixed length: the distance exists
    assert c["fixed_length"] is True


def test_the_planted_cliff_is_confirmed_by_the_data_one_character_down():
    """A cliff nobody can fall off is not a cliff. Folding one character shorter must
    actually destroy keys, or the length above is an artefact."""
    keys = [f"prefix-{i:03d}" for i in range(400)]
    assert identifier.fold_cost(keys, lambda k: k[:9])["keys_lost"] == 360
    assert identifier.fold_cost(keys, lambda k: k[:10])["keys_lost"] == 0


def test_control_the_cliff_moves_when_the_planted_prefix_is_removed():
    """The control that makes the test above a measurement: same size, no shared prefix,
    and the cliff drops from 9 to where unstructured keys collide."""
    keys = [_hashed(i)[:10] for i in range(400)]
    assert identifier.collides_at_length(keys) < 9


def test_a_cliff_exists_in_nearly_every_store_so_it_is_not_an_alarm():
    """The rule `headroom == 1 && keys_lost == 0` was proposed and withdrawn: evaluated at
    the cliff fold it fires on unstructured keys too, because a cliff exists somewhere in
    almost any population. So the cliff is REPORTED, never used as a bare alarm."""
    keys = [_hashed(i)[:identifier.ID_LENGTH] for i in range(400)]
    c = identifier.cliff_block(keys, fold=identifier.ID_LENGTH)
    assert c["collides_at_length"] is not None         # yes, unstructured keys have one
    assert c["distance_to_cliff"] >= 6                 # and it is nowhere near the fold


# ── silence has causes, and they mean opposite things ──────────────────────

#: The shape of this store's own `code_nodes.id`: a shared package prefix, variable
#: depth, and enough entropy per position that the space estimate explodes. Measured on
#: the real table: 602 keys, 8..106 chars, cliff at 56, threshold 1.09e28.
CODEGRAPH_SHAPED = [
    f"omni_memory/{'sub/' * (i % 9)}mod_{_hashed(i)[:6]}.py::fn_{_hashed(i)[6:9 + i % 11]}"
    for i in range(300)]


def test_structured_keys_make_the_at_scale_verdict_unreachable_at_every_fold():
    """The finding that killed verdict-gating. On keys shaped like this store's code
    graph the threshold at the first clean fold is astronomically larger than any
    population a laptop will hold, so a cliff warning gated on ZERO_AT_SCALE is not
    quiet — it is dead code, and stays dead however the store grows."""
    keys = CODEGRAPH_SHAPED
    reachable = []
    for L in range(1, max(len(k) for k in keys) + 1):
        cost = identifier.fold_cost(keys, lambda k, L=L: k[:L])
        space = identifier.measured_space(keys, L)
        v = identifier.fold_verdict(
            cost, space, identifier.population_threshold(space["space"]))
        if v["verdict"] == "ZERO_AT_SCALE":
            reachable.append(L)
    assert reachable == []
    c = identifier.cliff_block(keys, fold=None)
    assert c["reason_empty"] == "threshold_unreachable_at_fold"
    assert c["threshold_at_first_clean_fold"] > len(keys) * 10 ** 6


def test_control_a_fixed_length_population_can_reach_the_at_scale_verdict():
    """The paired control. Without it, `reachable == []` above is indistinguishable from a
    verdict function that never returns ZERO_AT_SCALE at all."""
    keys = [_hashed(i)[:8] for i in range(400)]
    reachable = []
    for L in range(1, 9):
        cost = identifier.fold_cost(keys, lambda k, L=L: k[:L])
        space = identifier.measured_space(keys, L)
        if identifier.fold_verdict(
                cost, space,
                identifier.population_threshold(space["space"]))["verdict"] == "ZERO_AT_SCALE":
            reachable.append(L)
    assert reachable                                   # it fires when it should


def test_silence_because_nothing_collides_is_a_different_reason():
    """Same empty warning, opposite meaning — which is why the reason is reported."""
    assert identifier.cliff_block(["a", "b", "c"], fold=1)["reason_empty"] == \
        "no_fold_merges_these_keys"


def test_a_tiny_population_is_blocked_by_saturation_not_by_the_threshold():
    """The third silence: three keys mean a position's alphabet equals the sample size,
    so the space estimate is the sample size in disguise."""
    c = identifier.cliff_block(["xa", "xb", "xc"], fold=2)
    assert c["collides_at_length"] == 1                # they DO merge at one character
    assert c["reason_empty"] == "positions_saturated"


# ── one number cannot describe a variable-length population ────────────────

def test_the_cliff_describes_the_right_edge_of_a_variable_length_population():
    """Most of these keys are SHORTER than the cliff, so for them the fold is the identity
    and no distance to the cliff exists. Reporting 'cliff = N' alone would read as a claim
    about the whole population."""
    keys = [f"pkg/{'sub/' * (i % 12)}mod{i}.py" for i in range(200)]
    c = identifier.cliff_block(keys, fold=None)
    assert c["fixed_length"] is False
    assert c["key_length_min"] < c["key_length_max"]
    assert c["keys_shorter_than_cliff"] > 0
    assert c["distance_to_cliff"] is None              # undefined, not zero
    assert "not folded" in c["distance_why_not"]


def test_control_on_fixed_length_keys_the_single_number_does_apply():
    """The pair for the test above: same metric, a population where the cliff and the loss
    curve carry the same information — which is why the limit was easy to miss."""
    keys = [f"{i:012x}" for i in range(200)]
    c = identifier.cliff_block(keys, fold=identifier.ID_LENGTH)
    assert c["fixed_length"] is True
    assert c["keys_shorter_than_cliff"] == 0
    assert isinstance(c["distance_to_cliff"], int)


def test_undersampled_positions_are_counted_on_variable_length_keys():
    """A position past most keys' end is estimated from the few keys long enough to have
    it, and that thin subsample still multiplies into the space product. It is what drives
    the threshold to absurd magnitudes, so it is named rather than left to be inferred."""
    varied = identifier.measured_space(["a", "ab", "abc", "abcd"], 4)
    fixed = identifier.measured_space(["aaaa", "bbbb", "cccc"], 4)
    assert varied["positions_undersampled"] > 0
    assert fixed["positions_undersampled"] == 0


def test_the_loss_curve_shows_what_the_cliff_projects_away():
    """At a short fold this population is destroyed; at the cliff it loses a couple of
    keys. Both are true of the same store, and the cliff alone reports only the second."""
    keys = [f"src/components/ui/widget_{i}/index.tsx" for i in range(300)]
    curve = identifier.loss_curve(keys)
    assert curve[0]["keys_lost"] > curve[-1]["keys_lost"]
    assert curve[-1]["keys_lost"] == 0                 # the full length keeps them apart


# ── commitment: validity and sufficiency are two questions ─────────────────

def test_commitment_is_order_independent():
    """A test, not an assumption: two enumerations of one population must commit alike."""
    assert identifier.population_commitment(["b", "a"])["digest"] == \
        identifier.population_commitment(["a", "b"])["digest"]


def test_a_same_size_swap_is_caught_and_named_apart_from_a_size_change():
    """The case the cheap implementation of the invariant gets wrong: comparing counts
    reports valid for a population that was swapped wholesale."""
    pinned = identifier.population_commitment([f"k{i}" for i in range(50)])
    swapped = [f"k{i}" for i in range(49)] + ["OTHER"]
    r = identifier.verify_commitment(pinned, swapped)
    assert r["status"] == "POPULATION_SWAPPED" and not r["valid"]
    assert pinned["count"] == len(swapped)             # a count check would have passed


def test_a_size_change_is_reported_as_changed_not_swapped():
    pinned = identifier.population_commitment([f"k{i}" for i in range(50)])
    r = identifier.verify_commitment(pinned, [f"k{i}" for i in range(49)])
    assert r["status"] == "POPULATION_CHANGED"


def test_control_an_unchanged_population_verifies():
    keys = [f"k{i}" for i in range(50)]
    assert identifier.verify_commitment(
        identifier.population_commitment(keys), list(reversed(keys)))["valid"]


def test_two_tenants_never_read_the_same_bytes_as_agreement():
    """The tenant compared is the CALLER's, not the pin's. Taking it from the pin is how
    the check quietly agrees with itself — identical bytes, two owners, reported valid."""
    pinned = identifier.population_commitment(["a", "b"], tenant="alice")
    r = identifier.verify_commitment(pinned, ["a", "b"], tenant="bob")
    assert r["status"] == "FOREIGN_SCOPE" and not r["valid"]
    assert identifier.verify_commitment(pinned, ["a", "b"], tenant="alice")["valid"]


def test_a_valid_commitment_can_be_insufficient_for_the_predicate_asked_of_it():
    """Cryptographic validity is not evidentiary sufficiency. A commitment over keys
    carries a headroom measurement forward correctly and cannot see a content change, so
    it would verify clean while answering a question it has no fields for."""
    keys = [f"k{i}" for i in range(20)]
    c = identifier.population_commitment(keys, verifies="headroom")
    assert identifier.verify_commitment(c, keys)["valid"]          # question one: yes
    assert identifier.sufficient_for(c, "headroom")["sufficient"]  # question two: yes
    insufficient = identifier.sufficient_for(c, "observation_current")
    assert not insufficient["sufficient"]                          # ...and here: no
    assert insufficient["missing"] == ["digest"]


def test_a_contract_version_bump_is_not_reported_as_a_population_change():
    """A measurement minted by a different contract must be remeasured, not compared —
    reporting it as a population change would send a consumer after the wrong cause."""
    pinned = dict(identifier.population_commitment(["a", "b"]), contract_version=0)
    assert identifier.verify_commitment(pinned, ["a", "b"])["status"] == \
        "CONTRACT_VERSION_CHANGED"


# ── both populations the store keys on, not just the tidy one ──────────────

def test_both_key_populations_are_measured(store, repo):
    """`memory.id` is hash-shaped and folded; `code_nodes.id` is structured, variable
    length and never folded. Reporting only the first is how a store looks clean while its
    larger, structurally riskier population sits outside the instrument."""
    from omni_memory.store import Memory
    codegraph.build_code_graph(store, repo)
    for i in range(5):
        store.add_memory(Memory(text=f"m{i}", kind="fact", branch="main"))
    pops = {p["population"]: p for p in identifier.populations(store)}
    assert set(pops) == {"memory.id", "code_nodes.id"}
    assert pops["memory.id"]["folded_at"] == identifier.ID_LENGTH
    assert pops["code_nodes.id"]["folded_at"] is None       # nothing truncates it
    assert pops["code_nodes.id"]["fixed_length"] is False


def test_the_contract_accepts_the_fold_the_caller_actually_uses(store, repo):
    """Two lengths we picked are not a claim about anyone else's keys."""
    from omni_memory.store import Memory
    for i in range(5):
        store.add_memory(Memory(text=f"m{i}", kind="fact", branch="main"))
    c = identifier.identifier_contract(store, prefix_folds=[6, 12])
    assert set(c["cliff"]) == {"6", "12"}
    assert c["cliff"]["6"]["fold"] == 6


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
