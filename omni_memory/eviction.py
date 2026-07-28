"""Memory hygiene — quarantine false/dead memory, purge only after a long grace.

The problem: a memory captured on a feature branch that was abandoned (never
merged, just deleted or left to rot) stays `active` and pollutes retrieval
forever. More generally, memories go false when their code changes underneath
them. This computes a composite eviction score per memory and quarantines the
losers — reversibly. Hard deletion is never automatic.

    evict_score = branch_dormancy        (abandoned .6 / dormant .35)
                + stale_age              (long ⚠stale + uncited)
                + kind_decay             (assumption/todo rot faster)
                + (1 - confidence)·0.1
                - citation_shield        (cited, esp. recently → protected)

Score ≥ QUARANTINE → status='abandoned' (out of inject/recall, still browsable,
`omni-memory restore`-able). Purge only removes memories quarantined > grace days
that were never cited, and only when explicitly asked (`gc --purge`).

The citation shield means a memory the agent keeps using never gets evicted,
even on a dead branch — because it's demonstrably still true.
"""
from __future__ import annotations

import math
import time
from pathlib import Path

from .store import Store

QUARANTINE = 0.5
_KIND_DECAY = {"assumption": 0.15, "todo": 0.10}


def evict_score(m: dict, branch_state: str, now: float) -> float:
    score = 0.0
    if branch_state == "abandoned":
        score += 0.6
    elif branch_state == "dormant":
        score += 0.35

    uses = m.get("uses") or 0
    if m.get("stale") and uses == 0:
        since = m.get("stale_since")
        days = (now - since) / 86400 if since else 0.0
        score += min(0.30, 0.10 + days / 120 * 0.20)

    score += _KIND_DECAY.get(m.get("kind"), 0.0)
    score += (1.0 - float(m.get("confidence") or 0.8)) * 0.1

    if uses > 0:  # citation shield — proven-useful memory resists eviction
        score -= min(0.5, 0.15 * math.log(1 + uses))
        lu = m.get("last_used")
        if lu and now - lu < 30 * 86400:
            score -= 0.2

    return max(0.0, min(1.0, score))


def sweep(store: Store, root: Path, dry_run: bool = False,
          purge: bool = False) -> dict:
    """Classify branches, quarantine eviction candidates, optionally purge."""
    from . import branch as branchmod
    branchmod.classify_branches(store, root)
    bstate = {b["name"]: b["status"] for b in store.branches()}
    now = time.time()
    threshold = float(store.get_meta("quarantine_threshold", QUARANTINE))

    candidates = []
    for r in store.db.execute("SELECT * FROM memory WHERE status='active'"):
        m = dict(r)
        st = bstate.get(m["branch"], "active")
        s = evict_score(m, st, now)
        if s >= threshold:
            reason = f"{st} branch" if st in ("abandoned", "dormant") else "stale"
            candidates.append({"id": m["id"], "branch": m["branch"], "state": st,
                               "score": round(s, 2), "reason": reason,
                               "text": m["text"][:70]})

    grace = float(store.get_meta("purge_grace_days", 90)) * 86400
    purgeable = store.purgeable(now - grace)

    if not dry_run:
        for c in candidates:
            store.quarantine(c["id"], f"{c['reason']} (score {c['score']})")
    purged = []
    if purge and not dry_run and purgeable:
        purged = [p["id"] for p in purgeable]
        store.delete(purged)

    return {"quarantined": candidates, "purgeable": purgeable, "purged": purged,
            "dry_run": dry_run}
