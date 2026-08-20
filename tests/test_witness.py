"""VERIFY → USE: carrying a verification forward to the moment of action.

Each of the four honesty rules from the provenance thread is asserted here, and each is
paired with the control that stops it degrading into a rubber stamp. The rule most often
gotten wrong is RULE 1 — `if not mismatches: return ok` is the natural shape of the code
and cannot tell "I checked and found nothing" from "I checked nothing".
"""
from __future__ import annotations

from omni_memory import gitmeta, inject, session_memory as sm, witness
from omni_memory.graph import build as codegraph
from omni_memory.store import Memory


def _pulled(store, repo, mem):
    """Pull a memory the way retrieval does, pinning what it is trusted on."""
    witness.pin(store, repo, [store.get_memory(mem.id)])
    return mem


# ── the window itself ───────────────────────────────────────────────────────

def test_a_source_that_moves_after_the_pull_is_stale_at_use(store, repo):
    """The failure this module exists for: verified at pull, acted on later, and the
    world moved in between. `⚠STALE` cannot see this — it is refresh-time."""
    m = sm.remember(store, repo, "create_order publishes after insert", kind="flow",
                    files=["svc.py"], symbols=["create_order"], source="manual")
    _pulled(store, repo, m)
    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\n# moved\n")
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] is True
    assert len(r["stale_at_use"]) == 1
    assert r["stale_at_use"][0]["path"] == "svc.py"
    assert r["orphaned_at_use"] == []          # it moved, it did not vanish


def test_control_an_unchanged_source_must_not_cry_wolf(store, repo):
    """If a steady source ever reported stale, the field would be noise within a week
    and every assertion above would be measuring a constant."""
    m = sm.remember(store, repo, "create_order publishes", kind="flow",
                    files=["svc.py"], symbols=["create_order"], source="manual")
    _pulled(store, repo, m)
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] is True
    assert r["stale_at_use"] == [] and r["orphaned_at_use"] == []
    assert "unchanged" in r["summary"]


# ── RULE 1: a witness that bound nothing never reports clean ────────────────

def test_a_witness_that_bound_nothing_says_the_world_was_not_checked(store, repo):
    """The assertion most often gotten wrong. A memory whose sources could not be pinned
    has had NOTHING verified — reporting that as clean is the exact false confidence the
    window exists to remove."""
    m = sm.remember(store, repo, "claim about a file that isn't there", kind="fact",
                    files=["never-existed.py"], source="manual")
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] is False
    assert r["stale_at_use"] == []             # no findings...
    assert "NOT checked" in r["summary"]       # ...but emphatically not "clean"
    assert r["bound"] == 0 and r["total"] == 1


def test_control_a_bound_witness_with_no_findings_is_genuinely_valid(store, repo):
    """Control for RULE 1: 'nothing found' must still be distinguishable from 'nothing
    checked', in the direction that matters."""
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    r = witness.verify(store, repo, [m.id])
    assert r["valid"] is True and r["stale_at_use"] == []


# ── RULE 2: coverage is a fraction, and the gap is named ───────────────────

def test_half_covered_cannot_read_as_fully_covered(store, repo):
    m = sm.remember(store, repo, "spans a real and a missing file", kind="fact",
                    files=["svc.py", "ghost.py"], source="manual")
    _pulled(store, repo, m)
    r = witness.verify(store, repo, [m.id])
    assert r["sources_bound"] == "1/2"
    assert [u["path"] for u in r["unbound"]] == ["ghost.py"]
    assert r["valid"] is True                  # something WAS checked — just not all


# ── RULE 3: vanished is not moved — different remedy, different field ──────

def test_a_vanished_source_is_orphaned_at_use_not_stale_at_use(store, repo):
    """Folding these together loses the instruction: a moved source wants revalidation,
    a deleted one wants re-sourcing."""
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    (repo / "svc.py").unlink()
    r = witness.verify(store, repo, [m.id])
    assert len(r["orphaned_at_use"]) == 1
    assert r["stale_at_use"] == []             # explicitly NOT stale


def test_both_axes_can_fire_for_one_memory_independently(store, repo):
    """A memory citing two sources — one moved, one gone — must expose both, not let the
    first verdict pre-empt the second."""
    (repo / "other.py").write_text("def helper():\n    return 2\n")
    m = sm.remember(store, repo, "spans two sources", kind="fact",
                    files=["svc.py", "other.py"], source="manual")
    _pulled(store, repo, m)
    (repo / "svc.py").write_text("# rewritten\n")
    (repo / "other.py").unlink()
    r = witness.verify(store, repo, [m.id])
    assert len(r["stale_at_use"]) == 1 and len(r["orphaned_at_use"]) == 1


# ── RULE 4 / binding kind travels with the pin ─────────────────────────────

def test_a_pin_the_agent_never_read_is_pin_time_bound_and_says_so(store, repo):
    """Only the reader knows what it read. A pin we hashed ourselves answers whether the
    source moved since the check — never whether the memory was right about the bytes it
    read — and the report must carry that limit."""
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    r = witness.verify(store, repo, [m.id])
    assert r["pin_time_bound"] == 1 and r["observation_bound"] == 0
    assert r["limits"] and "not whether the memory was right" in r["limits"][0]


def test_a_pin_backed_by_the_read_ledger_is_observation_bound(store, repo):
    """Control: when the agent DID read those exact bytes, the stronger binding is
    recorded and the caveat drops away."""
    store.read_ledger_put("svc.py", gitmeta.blob_sha(repo, "svc.py"))
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    r = witness.verify(store, repo, [m.id])
    assert r["observation_bound"] == 1 and r["pin_time_bound"] == 0
    assert r["limits"] == []


# ── wiring: pinning happens at the real retrieval point ────────────────────

def test_pulling_a_block_pins_what_it_is_trusted_on(store, repo):
    """The pin must happen where verification actually happens — `inject.build_block` —
    or the window stays open no matter how good the checker is."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "create_order publishes an event", kind="flow",
                files=["svc.py"], symbols=["create_order"], source="manual")
    assert store.witness_count() == 0
    assert inject.build_block(store, repo, query="create_order")
    assert store.witness_count() >= 1


def test_pins_do_not_survive_into_the_next_session(store, repo):
    """A pin from a prior session cannot answer 'did the world move during THIS task' —
    carrying it over would date the window wrongly, the same class as the cross-session
    read-ledger leak."""
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    assert store.witness_count() == 1
    store.clear_witness()                      # SessionStart does this
    assert store.witness_count() == 0
    assert witness.verify(store, repo, [m.id])["valid"] is False


def test_a_detected_window_leaves_a_durable_note(store, repo):
    """Detected after the fact still matters: it says the memory the agent ACTED on had
    already moved, which the next session needs to know."""
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    (repo / "svc.py").write_text("# rewritten\n")
    assert witness.note_on_use(store, repo, [m.id])
    assert any("stale-at-use" in e["summary"] for e in store.recent_events(5))


def test_control_no_window_leaves_no_note(store, repo):
    """Control: an event on every use would drown the signal it exists to carry."""
    m = sm.remember(store, repo, "svc note", kind="fact", files=["svc.py"], source="manual")
    _pulled(store, repo, m)
    assert witness.note_on_use(store, repo, [m.id]) is None
    assert not any("stale-at-use" in e["summary"] for e in store.recent_events(5))


def test_an_unpinned_memory_never_manufactures_a_note(store, repo):
    """RULE 1 again, at the event layer: nothing was checked, so nothing may be claimed
    — in either direction."""
    m = store.add_memory(Memory(text="no anchor at all", kind="decision", branch="main"))
    assert witness.note_on_use(store, repo, [m.id]) is None
