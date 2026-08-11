"""The 0.9.20 gap-closers: conversational-noise rejection + retro sweep (P0),
chain-complete retrieval (P1), protected/constitutional memory (P2), the
cross-session event bus and user/feedback kinds (P3)."""
from __future__ import annotations

from omni_memory import cleanup, eviction, inject, rank
from omni_memory.store import Memory


# ── P0: conversational / thread noise ──────────────────────────────────────

def test_chatter_is_noise():
    chat = [
        "@DanceNitra This is the useful distinction between provenance and schema",
        "mikeadolan commented on Apr 8 about the raw-capture approach",
        "safal207 replied 3 hours ago with the six-verdict mapping",
        "https://github.com/DanceNitra/agora/blob/main/probe.py",
        "See github.com/foo/bar/issues/34556 for the full thread",
        "(Drafted by Agora, an autonomous research OS, posted with approval)",
    ]
    for t in chat:
        assert cleanup.is_noise(t, source="session"), f"should be noise: {t}"
    # control: a real anchored fact with a single @package mention is NOT noise
    assert not cleanup.is_noise(
        "GeminiLiveSession wraps @google/genai live.connect in gemini-client.ts",
        files=["src/lib/gemini-client.ts"], source="session")


def test_heuristic_capture_requires_anchor(store, repo):
    """The heuristic transcript scanner is strict — anchorless chatter is dropped,
    a concretely-anchored line survives."""
    from omni_memory import session_memory as sm
    items = [
        {"text": "we should really nail the memory story down someday", "kind": "todo"},  # no anchor
        {"text": "publish order.created to the orders_topic kafka bus", "kind": "flow"},   # anchored
    ]
    from omni_memory import cleanup as cl
    kept, dropped = cl.filter_items(items, source="heuristic")
    assert dropped == 1 and len(kept) == 1
    assert "kafka" in kept[0]["text"]


def test_gc_noise_sweep_quarantines_thread_junk(store, repo):
    from omni_memory.store import Memory
    junk = store.add_memory(Memory(
        text="@safal207 the RAMR/LS split maps one-to-one onto the four verdicts",
        kind="flow", branch="main"))
    real = store.add_memory(Memory(text="orders table has a unique idx on email",
                                   kind="db", branch="main", files=["db.sql"]))
    sw = eviction.sweep(store, repo, dry_run=False)
    assert junk.id in {c["id"] for c in sw["noise"]}
    assert store.get_memory(junk.id)["status"] == "abandoned"   # quarantined (reversible)
    assert store.get_memory(real.id)["status"] == "active"       # kept


# ── P1: chain-complete retrieval ───────────────────────────────────────────

def test_linked_memories_travel_together(store, repo):
    """A decision and the gotcha on the SAME symbol must be retrieved as a unit,
    not fragmented by a top-N ranking slice."""
    dec = store.add_memory(Memory(text="create_order inserts the row then publishes",
                                  kind="decision", branch="main", symbols=["create_order"]))
    got = store.add_memory(Memory(text="never charge before the create_order row commits",
                                  kind="gotcha", branch="main", symbols=["create_order"]))
    unrelated = store.add_memory(Memory(text="config loader reads yaml", kind="fact",
                                        branch="main", symbols=["load_config"]))
    linked = store.linked_memories(store.get_memory(dec.id), exclude={dec.id})
    ids = {m["id"] for m in linked}
    assert got.id in ids
    assert unrelated.id not in ids


def test_inject_pulls_the_chain(store, repo):
    # cap retrieval at 1 so the sibling can only appear if the chain pulls it in
    store.set_meta("inject_max_items", 1)
    store.add_memory(Memory(text="create_order inserts the row then publishes an event",
                            kind="decision", branch="main", symbols=["create_order"], files=["svc.py"]))
    store.add_memory(Memory(text="never charge before the create_order row is committed",
                            kind="gotcha", branch="main", symbols=["create_order"], files=["svc.py"]))
    block = inject.build_block(store, repo, query="create_order")
    assert "inserts the row" in block and "never charge" in block   # both hops present
    assert "↳" in block                                             # the second came via the chain


# ── P2: protected / constitutional memory ──────────────────────────────────

def test_protected_never_evicted():
    import time
    now = time.time()
    base = {"kind": "fact", "confidence": 0.8, "uses": 0, "stale": 1,
            "stale_since": now - 400 * 86400, "evidence": "inferred"}
    assert eviction.evict_score(base, "abandoned", now) > 0            # normally evictable
    assert eviction.evict_score({**base, "protected": True}, "abandoned", now) == 0.0


def test_protected_survives_gc(store, repo):
    m = store.add_memory(Memory(text="ARCH: all writes go through the outbox table",
                                kind="decision", branch="feature-dead", files=["db.sql"]))
    store.set_protected(m.id, True)
    eviction.sweep(store, repo, dry_run=False)          # even on a dead branch
    assert store.get_memory(m.id)["status"] == "active"  # not quarantined


def test_protected_outranks_equal_peer(store, repo):
    a = store.add_memory(Memory(text="alpha widget config toggle", kind="fact", branch="main"))
    b = store.add_memory(Memory(text="beta widget config toggle", kind="fact", branch="main"))
    store.set_protected(b.id, True)                      # ×1.3 standing lift
    ranked = rank.rank(store.memories(status="active"), "widget config toggle")
    assert ranked[0]["id"] == b.id                      # protected wins the otherwise-even tie


# ── P3: event bus + new kinds ──────────────────────────────────────────────

def test_event_bus_roundtrip(store):
    store.add_event("main", "captured 5 memories")
    store.add_event("feature-x", "captured 2 memories (+1 cited)")
    evs = store.recent_events(3)
    assert evs[0]["summary"] == "captured 2 memories (+1 cited)"      # newest first
    assert evs[0]["branch"] == "feature-x"
    assert len(evs) == 2


def test_user_and_feedback_kinds_rank_high(store, repo):
    store.add_memory(Memory(text="the user prefers terse answers and no emoji",
                            kind="user", branch="main"))
    store.add_memory(Memory(text="feedback: never mock the database in tests, we got burned",
                            kind="feedback", branch="main"))
    store.add_memory(Memory(text="a plain fact about something", kind="fact", branch="main"))
    ranked = rank.rank(store.memories(status="active"), "user database tests answers")
    assert ranked[0]["kind"] in ("feedback", "user")                 # correction/identity float up
