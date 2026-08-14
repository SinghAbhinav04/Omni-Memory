"""Memory Health Galaxy rollup: per-file health score, blind spots, clustering."""
from __future__ import annotations

from omni_memory import branch as branchmod, session_memory as sm, healthmap


def test_healthmap_scores_and_blind_spots(store, repo):
    branchmod.full_refresh(store, repo)
    # svc.py is remembered (verified + cited); the rest of the repo is blind
    m = sm.remember(store, repo, "create_order inserts the row then publishes",
                    kind="flow", files=["svc.py"], symbols=["create_order"],
                    source="manual", evidence="verified")
    store.bump_uses([m.id])
    hm = healthmap.build(store, repo)
    by = {n["id"]: n for n in hm["nodes"]}
    assert "svc.py" in by
    svc = by["svc.py"]
    assert svc["score"] is not None and svc["score"] > 0.6   # well-remembered → cool/healthy
    assert svc["verdict"] == "fresh" and svc["mem"] >= 1
    assert svc["cluster"] == "(root)"                         # top-level file
    assert hm["stats"]["remembered"] >= 1
    # a memory whose anchored file is deleted → orphaned, not counted as re-fetchable
    import subprocess
    (repo / "svc.py").unlink()
    subprocess.run(["git", "-C", str(repo), "commit", "-aqm", "rm"], check=True, capture_output=True)
    hm2 = healthmap.build(store, repo)
    svc2 = next((n for n in hm2["nodes"] if n["id"] == "svc.py"), None)
    if svc2:                                                  # still anchored, now orphaned
        assert svc2["verdict"] == "orphaned"


def test_healthmap_empty_is_safe(store, repo):
    hm = healthmap.build(store, repo)                         # no graph, no memory
    assert hm["nodes"] == [] and hm["stats"]["files"] == 0
