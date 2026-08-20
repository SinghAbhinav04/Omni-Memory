"""The savings ledger — and the honesty constraints on it.

A savings metric is worth exactly as much as its baseline is defensible, so these tests
pin the conservative choices rather than just the arithmetic: unresolvable anchors earn
no credit, a measurement pass never counts itself, and the numbers reconcile.
"""
from omni_memory import inject, savings
from omni_memory import session_memory as sm


def _pull(store, repo, query="", event="pull"):
    return inject.build_block(store, repo, query=query, event=event)


def test_anchored_memory_beats_re_reading(store, repo):
    sm.remember(store, repo, "create_order publishes order.created after insert",
                kind="flow", files=["svc.py"], symbols=["create_order"])
    _pull(store, repo)
    g = savings.summary(store)
    assert g["pulls"] == 1
    assert g["served"] > 0
    # svc.py is ~700 bytes, so the baseline clears the flat search overhead
    assert g["baseline"] > savings.SEARCH_OVERHEAD
    assert g["saved"] == max(0, g["baseline"] - g["served"])
    assert 0 < g["pct"] <= 100


def test_unanchored_memory_earns_no_credit(store, repo):
    """A memory with no resolving source could not have been grepped for, so claiming
    we saved that read would be inventing the number."""
    sm.remember(store, repo, "the team prefers trunk-based development", kind="decision")
    _pull(store, repo)
    row = savings.history(store)[0]
    assert row["files"] == 0
    assert row["baseline"] == savings.SEARCH_OVERHEAD


def test_a_dead_anchor_earns_no_credit(store, repo):
    """Same rule when the source USED to exist: credit tracks what resolves today."""
    sm.remember(store, repo, "deleted_module owned the retry policy",
                kind="component", files=["gone.py"])
    _pull(store, repo)
    assert savings.history(store)[0]["files"] == 0


def test_measuring_does_not_record(store, repo):
    """`gain` builds a block to report the current footprint. If that counted as a
    pull, the metric would inflate itself every time anyone looked at it."""
    sm.remember(store, repo, "create_order inserts then publishes",
                kind="flow", files=["svc.py"])
    inject.build_block(store, repo, query="")          # no event= → measurement only
    assert savings.summary(store)["pulls"] == 0
    inject.build_block(store, repo, query="", event="pull")
    assert savings.summary(store)["pulls"] == 1


def test_events_are_distinguished(store, repo):
    sm.remember(store, repo, "create_order inserts then publishes",
                kind="flow", files=["svc.py"])
    _pull(store, repo, event="session")
    _pull(store, repo, event="pull")
    g = savings.summary(store)
    assert g["pulls"] == 2 and g["sessions"] == 1


def test_baseline_caps_a_huge_file(store, repo):
    (repo / "huge.py").write_text("x = 1\n" * 400_000, encoding="utf-8")
    base, n = savings.baseline_tokens(repo, [{"files": ["huge.py"]}])
    assert n == 1
    assert base == savings._FILE_CAP + savings.SEARCH_OVERHEAD


def test_a_file_counts_once_per_pull(store, repo):
    mems = [{"files": ["svc.py"]}, {"files": ["svc.py"]}]
    two, n2 = savings.baseline_tokens(repo, mems)
    one, n1 = savings.baseline_tokens(repo, mems[:1])
    assert (two, n2) == (one, n1)


def test_reset_clears_the_ledger(store, repo):
    sm.remember(store, repo, "create_order inserts", kind="flow", files=["svc.py"])
    _pull(store, repo)
    assert store.clear_savings() >= 1
    assert savings.summary(store)["pulls"] == 0


def test_human_is_compact():
    assert savings.human(812) == "812"
    assert savings.human(3_500) == "3.5k"
    assert savings.human(2_400_000) == "2.4M"
