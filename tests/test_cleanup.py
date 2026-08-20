"""Noise filter: keep dense/atomic memories (even longish), drop dumps + prose.
Storage-worthiness is decoupled from injection-size (injection truncates on its
own), so a useful longer memory survives capture."""
from omni_memory import cleanup, inject

LONG_USEFUL = (
    "On checkout, never call payments.charge() before the order row is committed — "
    "if the DB insert fails after a successful charge you get a phantom charge with "
    "no matching order, and the reconciliation job in billing/reconcile.py can't pair "
    "it. Commit the order first, then charge inside the same transaction boundary.")


def test_keeps_useful_longish_memory():
    assert 280 < len(LONG_USEFUL) <= 600          # would've been dropped by the old 280 cap
    assert not cleanup.is_noise(LONG_USEFUL, source="session")
    assert not cleanup.is_noise(LONG_USEFUL, source="doc")   # it has anchors (charge(), DB…)


def test_rejects_paragraph_dump_by_word_count():
    assert cleanup.is_noise(" ".join(["word"] * 80), source="session")


def test_still_rejects_structural_and_anchorless():
    assert cleanup.is_noise("## Overview", source="doc")
    assert cleanup.is_noise("How does auth work?", source="session")
    assert cleanup.is_noise("this handles the important business logic", source="doc")


def test_anchor_must_be_NAMED_in_the_text():
    """A `files` entry alone used to waive the strict anchor requirement. It cannot:
    `ingest_docs` stamps the file it is currently scanning onto every line it extracts,
    so the waiver made each line self-certifying and admitted whole paragraphs of
    PLAN.md as project memory. The anchor has to appear in the sentence."""
    assert cleanup.is_noise("handles the business logic", files=["a.py"], source="doc")
    assert not cleanup.is_noise("a.py handles the business logic",
                                files=["a.py"], source="doc")
    # a nested path is matched on its leaf, the way a sentence would mention it
    assert not cleanup.is_noise("store.py owns the schema",
                                files=["omni_memory/store.py"], source="doc")
    # ...and a trusted source is unaffected either way
    assert not cleanup.is_noise("handles the business logic", files=["a.py"],
                                source="session")


def test_prose_with_a_slash_is_not_a_path():
    """English writes `commit/switch` and `drift/delete`. Treating those as paths let
    ordinary prose certify itself as anchored."""
    assert cleanup.is_noise("commit/switch branch/delete and verify the states",
                            source="heuristic")
    assert not cleanup.is_noise("the handler lives in src/api/orders.py",
                                source="heuristic")


def test_inject_clip_is_word_boundary():
    c = inject._clip(LONG_USEFUL, 120)
    assert len(c) <= 121 and c.endswith("…")
    assert " " != c[-2]                            # not left dangling on a space
    assert c[:-1].strip() in LONG_USEFUL or c.split("…")[0].split()[-1] in LONG_USEFUL


def test_inject_clip_noop_when_short():
    assert inject._clip("short one", 200) == "short one"


def test_remember_many_reports_dropped(store, repo):
    from omni_memory import session_memory as sm
    items = [{"text": "## heading"},                         # noise
             {"text": "gotcha: never call charge() before the order row commits",
              "kind": "gotcha"}]                              # kept
    added, dropped = sm.remember_many(store, repo, items, source="session")
    assert added == 1 and dropped == 1
