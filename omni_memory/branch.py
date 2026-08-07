"""Branch-aware memory: sync git topology into the store, resolve scope."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from . import gitmeta
from .store import Store


def sync_git(store: Store, root: Path) -> dict:
    """Pull the current git topology into the store. Returns the snapshot."""
    snap = gitmeta.snapshot(root)
    for b in snap["branches"]:
        store.upsert_branch(**b)
    for c in snap["commits"]:
        store.upsert_commit(c["sha"], c["branch"], c["author"], c["date"],
                            c["message"], c["files"])
    store.set_meta("default_branch", snap["default"])
    classify_branches(store, root)
    from . import staleness
    staleness.recompute(store, root)
    return snap


def full_refresh(store: Store, root: Path) -> dict:
    """Everything the dashboard shows, rebuilt from the current repo state:
    the code graph, git topology, branch classification, and symbol-level
    staleness. This is what the auto-refresh watcher runs so the user never has
    to invoke `map`/`check`/`branches` by hand."""
    try:
        from .graph import build as codegraph
        codegraph.build_code_graph(store, root)
    except Exception:  # noqa: BLE001 — fall back to file-level staleness
        pass
    snap = sync_git(store, root)
    try:  # keep the IDE-agnostic AGENTS.md context current for the next session
        from . import agentsmd
        agentsmd.write(store, root)
    except Exception:  # noqa: BLE001
        pass
    return snap


def classify_branches(store: Store, root: Path) -> list[tuple]:
    """Keep branch statuses honest so eviction can act on them:

      merged     -> re-anchor its memories onto the base (they're base truth now)
      abandoned  -> was recorded, now gone from git refs, never merged (deleted)
      dormant    -> still exists but quiet > dormancy_days AND base moved ahead
      active     -> otherwise

    Returns the (name, new_status) transitions this pass.
    """
    if not gitmeta.is_repo(root):
        return []
    live = set(gitmeta.list_branches(root, include_remotes=True))
    base = store.get_meta("default_branch") or gitmeta.default_branch(root)
    cur = gitmeta.current_branch(root)
    now = time.time()
    dormancy = float(store.get_meta("dormancy_days", 60)) * 86400
    # Consider every branch we've recorded AND every branch a memory is tagged to
    # (so a memory on a deleted branch we never synced still gets caught).
    recorded = {b["name"]: b["status"] for b in store.branches()}
    tagged = {r["branch"] for r in store.db.execute(
        "SELECT DISTINCT branch FROM memory")}
    changed: list[tuple] = []
    for name in recorded.keys() | tagged:
        if not name or name == base:
            continue
        st = recorded.get(name, "active")
        if st == "merged":
            store.reanchor_branch(name, base)  # idempotent
            continue
        if name not in live:                    # deleted while unmerged
            new = "abandoned"
        else:
            _, behind = gitmeta.ahead_behind(root, name, base)
            age = now - gitmeta.tip_time(root, name)
            if name != cur and age > dormancy and behind > 0:
                new = "dormant"
            else:
                new = "active"
        if new != st:
            if name in recorded:
                store.update_branch_status(name, new)
            else:                                # branch we only knew via a memory
                store.upsert_branch(name=name, status=new)
            changed.append((name, new))
    return changed


def on_branch_change(store: Store, root: Path) -> None:
    """Re-sync topology (now also classifies + re-anchors merged branches)."""
    sync_git(store, root)


def scope(store: Store, root: Path) -> tuple[str, Optional[str]]:
    """(current_branch, base_branch|None) honoring the branch-aware toggle.

    branch-aware ON  → memory for current branch + its base.
    branch-aware OFF → all branches (base returned as None, current as '*').
    """
    if not store.get_meta("branch_aware", True):
        return "*", None
    cur = gitmeta.current_branch(root)
    base = store.get_meta("default_branch") or gitmeta.default_branch(root)
    return cur, (base if base != cur else None)


