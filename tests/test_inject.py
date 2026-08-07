"""Injection: budget caps, no-match widen, empty store, enforcement rules."""
from omni_memory import inject
from omni_memory.store import Memory


def test_empty_store_returns_blank(store, repo):
    assert inject.build_block(store, repo, query="anything") == ""


def test_block_has_rules_and_ids(store, repo):
    m = store.add_memory(Memory(text="checkout uses Stripe PaymentIntents",
                                kind="decision", branch="main"))
    block = inject.build_block(store, repo, query="stripe checkout")
    assert "VERIFIED PROJECT MEMORY" in block
    assert "not in memory" in block            # enforcement rules present
    assert f"[{m.id}]" in block


def test_item_cap_and_budget(store, repo):
    for i in range(40):
        store.add_memory(Memory(
            text=f"memory {i} " + "long descriptive sentence about a subsystem " * 4,
            kind="fact", branch="main"))
    block = inject.build_block(store, repo, query="")
    # never dump everything: capped well under 40 items and under the char budget
    body = [ln for ln in block.splitlines() if ln.startswith("[")]
    assert len(body) <= inject._MAX_ITEMS
    assert len(block) <= inject._CHAR_BUDGET + 400   # + header/rules overhead


def test_meta_override_lowers_budget(store, repo):
    for i in range(40):
        store.add_memory(Memory(text=f"topic fact {i} " + "detail " * 20,
                                kind="fact", branch="main"))
    big = inject.build_block(store, repo, query="topic")
    store.set_meta("inject_max_items", 3)
    store.set_meta("inject_char_budget", 500)
    small = inject.build_block(store, repo, query="topic")
    assert len(small) < len(big)
    assert len([ln for ln in small.splitlines() if ln.startswith("[")]) <= 3


def test_global_memory_injects_into_project(store, repo):
    from omni_memory.store import Store, Memory, global_dir
    gs = Store(exact_dir=global_dir())          # isolated to a temp dir by conftest
    gs.add_memory(Memory(text="ALWAYS prefer pathlib over os.path", kind="decision",
                         branch="global"))
    store.add_memory(Memory(text="this project uses FastAPI", kind="fact", branch="main"))
    block = inject.build_block(store, repo, query="file paths")
    assert "pathlib" in block and "🌐global" in block    # global travels in
    assert "FastAPI" in block                            # alongside project memory


def test_rules_are_compact(store, repo):
    # the enforcement rules ride every message → keep them short
    assert len(inject.ENFORCE_RULES) < 220


def test_no_match_widens_to_few(store, repo):
    for i in range(20):
        store.add_memory(Memory(text=f"topic-alpha fact {i}", kind="fact", branch="main"))
    block = inject.build_block(store, repo, query="zzz-nonexistent-term")
    body = [ln for ln in block.splitlines() if ln.startswith("[")]
    assert 0 < len(body) <= inject._WIDEN_ITEMS
    assert "top general memories" in block
