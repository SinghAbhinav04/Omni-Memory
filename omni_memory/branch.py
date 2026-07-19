"""Branch-aware memory: sync git topology into the store, resolve scope."""
from __future__ import annotations

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
    return snap


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


def on_branch_change(store: Store, root: Path) -> None:
    """Re-sync topology; flip merged branches' memories to 'merged' status."""
    snap = sync_git(store, root)
    for b in snap["branches"]:
        if b["status"] == "merged":
            store.db.execute(
                "UPDATE memory SET status='merged' "
                "WHERE branch=? AND status='active'", (b["name"],))
    store.db.commit()
