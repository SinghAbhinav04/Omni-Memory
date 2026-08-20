"""System Map: the model is a projection of the store, and its citations carry a
verdict re-resolved from the recorded blob shas — so the map can show which parts of
itself have gone stale rather than asserting freshness on the model's word.

The paired controls matter more than the happy path here: a map that renders a
confident building for a source that was deleted is worse than no map.
"""
from __future__ import annotations

import subprocess

from omni_memory import session_memory as sm, systemmap
from omni_memory.graph import build as codegraph
from omni_memory.store import Memory


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


# ── the pure verdict function: every branch, no repo needed ─────────────────

def test_verdict_fresh_when_every_recorded_sha_still_resolves():
    assert systemmap._verdict({"svc.py": "a" * 40}, {"svc.py": "a" * 40}) == "FRESH"


def test_verdict_drifted_when_content_changed():
    assert systemmap._verdict({"svc.py": "a" * 40}, {"svc.py": "b" * 40}) == "DRIFTED"


def test_verdict_orphaned_outranks_drift():
    """Worst anchor wins: a memory citing one changed file and one deleted file is
    ORPHANED, because the remedy is re-source, not re-derive."""
    v = systemmap._verdict({"a.py": "a" * 40, "gone.py": "c" * 40}, {"a.py": "b" * 40})
    assert v == "ORPHANED"


def test_verdict_unverifiable_is_not_fresh():
    """A memory with no recorded content key must never be *claimed* fresh — it is
    counted separately. This is the control against `no mismatches -> ok`."""
    assert systemmap._verdict({}, {"svc.py": "a" * 40}) == "UNVERIFIABLE"


def test_no_content_key_and_no_surviving_source_is_orphaned():
    """UNVERIFIABLE says "I cannot check the bytes". It must not be used to stay quiet
    about a source that is plainly gone — that renders a dead citation as benign
    legacy, which is the confidently-wrong failure this map exists to avoid."""
    assert systemmap._verdict({}, {}, ["gone.py"]) == "ORPHANED"
    # ...but one surviving declared source is still merely unverifiable
    assert systemmap._verdict({}, {"svc.py": "a" * 40}, ["svc.py"]) == "UNVERIFIABLE"


def test_no_evidence_at_all_is_not_fresh():
    """`_worst` folds a building's citation verdicts. Its identity element must not be
    FRESH: an empty set means nothing was checked, not that everything passed."""
    assert systemmap._worst([]) == "UNVERIFIABLE"
    assert systemmap._worst(["FRESH", "DRIFTED"]) == "DRIFTED"


# ── the model over a real repo ──────────────────────────────────────────────

def test_anchored_memory_builds_a_typed_building_with_citations(store, repo):
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "orders API creates and publishes", kind="endpoint",
                files=["svc.py"], symbols=["create_order"], source="manual")
    m = systemmap.build(store, repo)

    node = next(n for n in m["nodes"] if n["memCount"] > 0)
    assert node["kind"] == "gateway"                  # endpoint memory -> gatehouse
    assert node["zone"] == "entry"
    assert node["verdict"] == "FRESH"                 # source unchanged since capture
    # fixture integrity: the citation must actually resolve to the symbol's lines,
    # not a whole-file placeholder — otherwise this test proves nothing about spans.
    cite = node["citations"][0]
    assert cite["path"] == "svc.py"
    assert cite["endLine"] > cite["startLine"] > 1
    assert cite["proves"]                             # a citation carries its claim


def test_a_building_degrades_when_its_cited_source_changes(store, repo):
    """The whole point of the map: edit a cited source and the building must stop
    claiming FRESH. Paired with the control below, which must NOT degrade."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "service creates orders", kind="component",
                files=["svc.py"], symbols=["create_order"], source="manual")
    before = systemmap.build(store, repo)
    assert next(n for n in before["nodes"] if n["memCount"] > 0)["verdict"] == "FRESH"

    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\n# changed\n")
    after = systemmap.build(store, repo)
    assert next(n for n in after["nodes"] if n["memCount"] > 0)["verdict"] == "DRIFTED"


def test_control_an_untouched_source_keeps_its_building_fresh(store, repo):
    """Control for the test above — if an unrelated edit degraded the map too, the
    verdict would be noise rather than signal."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "service creates orders", kind="component",
                files=["svc.py"], symbols=["create_order"], source="manual")
    (repo / "other.py").write_text("x = 1\n")          # a change somewhere else entirely
    m = systemmap.build(store, repo)
    assert next(n for n in m["nodes"] if n["memCount"] > 0)["verdict"] == "FRESH"


def test_deleted_source_renders_orphaned_not_missing(store, repo):
    """Deletion is the half a diff can never catch: nothing later mentions the file.
    The building must survive and say ORPHANED rather than quietly vanishing."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "service creates orders", kind="component",
                files=["svc.py"], symbols=["create_order"], source="manual")
    (repo / "svc.py").unlink()
    m = systemmap.build(store, repo)
    node = next(n for n in m["nodes"] if n["memCount"] > 0)
    assert node["verdict"] == "ORPHANED"


def test_unremembered_module_is_a_visible_blind_spot(store, repo):
    """A cluster the code graph knows and memory doesn't must appear as BLIND with
    confidence 'unknown' — the un-mapped regions are load-bearing information, not
    something to omit so the map looks complete."""
    codegraph.build_code_graph(store, repo)
    m = systemmap.build(store, repo)
    blind = [n for n in m["nodes"] if n["verdict"] == "BLIND"]
    assert blind and all(n["confidence"] == "unknown" and n["memCount"] == 0 for n in blind)


def test_not_bindable_counted_beside_the_buildings_never_inside(store, repo):
    """A memory with no file/symbol anchor can never be placed on the map. It is
    reported beside the model, not silently dropped and not faked into a building."""
    store.add_memory(Memory(text="we use event sourcing", kind="decision", branch="main"))
    m = systemmap.build(store, repo)
    assert m["stats"]["notBindable"] == 1
    assert all(n["memCount"] == 0 for n in m["nodes"])   # it produced no building


def test_symbol_only_memory_is_placed_not_vanished(store, repo):
    """The skip guard needs BOTH anchors empty, but placement only ever read `files` —
    so a symbol-anchored memory was neither drawn on a building nor counted as
    not-bindable. It fell out of the accounting entirely."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "create_order publishes after insert", kind="flow",
                symbols=["create_order"], source="manual")
    m = systemmap.build(store, repo)
    placed = [n for n in m["nodes"] if n["memCount"] > 0]
    assert placed, "symbol-anchored memory produced no building"
    assert m["stats"]["notBindable"] == 0
    assert placed[0]["citations"][0]["path"] == "svc.py"   # resolved via the code graph


def test_stats_flows_matches_the_flows_shipped(store, repo):
    """The header and the CLI both print `stats.flows`. Counting before the slice
    promised flows the picker could never offer."""
    for i in range(12):
        sm.remember(store, repo, f"step{i} -> validate -> store{i}", kind="flow",
                    files=["svc.py"], source="manual")
    m = systemmap.build(store, repo)
    assert m["stats"]["flows"] == len(m["flows"]) <= 8


def test_confidence_observed_requires_both_top_evidence_and_fresh_sources(store, repo):
    """`observed` is earned, not asserted: a memory can be `verified` and still sit on
    a drifted source, in which case the building must NOT claim observed."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "service creates orders", kind="component",
                files=["svc.py"], symbols=["create_order"], source="manual",
                evidence="verified")
    fresh = systemmap.build(store, repo)
    assert next(n for n in fresh["nodes"] if n["memCount"] > 0)["confidence"] == "observed"

    (repo / "svc.py").write_text("# rewritten\n")
    drifted = systemmap.build(store, repo)
    node = next(n for n in drifted["nodes"] if n["memCount"] > 0)
    assert node["verdict"] == "DRIFTED"
    assert node["confidence"] == "inferred"            # top tier alone is not enough


def test_arrow_chain_parses_into_flow_steps(store, repo):
    """`A -> B -> C` is the shape the extractor is told to produce for endpoint maps,
    so it must become a steppable causal flow rather than one opaque label."""
    sm.remember(store, repo, "POST /orders -> validate -> insert row -> publish event",
                kind="flow", files=["svc.py"], source="manual")
    m = systemmap.build(store, repo)
    flow = m["flows"][0]
    assert [s["action"] for s in flow["steps"]] == [
        "POST /orders", "validate", "insert row", "publish event"]


def test_prose_memory_does_not_become_a_bogus_flow(store, repo):
    """Control for the flow parser: a memory with no arrow chain has no causal steps
    to show, so it must produce no flow at all rather than a one-step stub."""
    sm.remember(store, repo, "the service handles orders carefully", kind="flow",
                files=["svc.py"], source="manual")
    assert systemmap.build(store, repo)["flows"] == []


# ── the standalone artifact ────────────────────────────────────────────────

def test_artifact_makes_no_runtime_network_request(store, repo):
    """The saved map must stay inspectable after the checkout it describes is gone —
    and must not be able to phone home about what it describes. So: no fetch, no
    XHR/WebSocket, no external script or stylesheet."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "service creates orders", kind="component",
                files=["svc.py"], symbols=["create_order"], source="manual")
    html = systemmap.artifact(store, repo)
    body = html.replace("async function api(){ return SM_MODEL; }", "")
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "<script src=", "<link"):
        assert banned not in body, f"artifact reaches the network via {banned!r}"


def test_artifact_shares_one_renderer_with_the_dashboard(store, repo):
    """The artifact lifts its CSS and renderer out of index.html between sentinels, so
    the two surfaces cannot drift. If the sentinels move, this must fail loudly rather
    than silently shipping a map that renders differently from the dashboard."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "svc note", kind="component", files=["svc.py"], source="manual")
    html = systemmap.artifact(store, repo)
    src = systemmap._STATIC.read_text(encoding="utf-8")
    marker = "function bandPts" if "function bandPts" in src else "bandPts"
    assert marker in html and marker in src          # same code, one source
    assert "#sm{" in html                            # and the same stylesheet


def test_missing_sentinel_fails_loudly_rather_than_shipping_a_broken_map(store, repo, monkeypatch):
    """Control for the test above: a silently-empty extraction would produce an artifact
    that opens to a blank page. It must raise instead."""
    import pytest
    monkeypatch.setattr(systemmap, "_STATIC", repo / "no-markers.html")
    (repo / "no-markers.html").write_text("<html>nothing here</html>")
    with pytest.raises(RuntimeError, match="sentinel"):
        systemmap.artifact(store, repo)


def test_extractor_does_not_leak_the_sentinel_line_into_the_output(store, repo):
    """The slice starts at the END of the marker's line. Prose sharing that line would
    land in the extracted JS as bare text — a syntax error visible only in the artifact,
    never in the dashboard."""
    codegraph.build_code_graph(store, repo)
    sm.remember(store, repo, "svc note", kind="component", files=["svc.py"], source="manual")
    html = systemmap.artifact(store, repo)
    assert "SM-JS:START" not in html and "SM-CSS:START" not in html


def test_inline_json_cannot_close_the_host_script_element(store, repo):
    """Memory text is teammate-controlled (committed team shards), so it reaches the
    artifact as untrusted input. Serialization must neutralize `<`."""
    store.add_message = None
    sm.remember(store, repo, "danger </script><img src=x onerror=alert(1)>", kind="fact",
                files=["svc.py"], source="manual")
    out = systemmap.to_json(systemmap.build(store, repo))
    assert "</script>" not in out
    assert "\\u003c" in out
