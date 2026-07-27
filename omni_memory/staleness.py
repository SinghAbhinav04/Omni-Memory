"""Staleness anchoring — flag memories whose code has changed underneath them.

A memory is anchored to the commit it was written at (`commit_range`) and the
files it describes. When those files change in later commits, the memory may no
longer be true. Rather than walk an AST call-graph, we walk git history — for
each memory, is any of its files touched between its anchor commit and HEAD?

Flagged memories keep their content (never auto-deleted — the code changing does
not prove the *decision* wrong) but are marked so inject/recall/dashboard can
show "⚠ may be stale, re-verify". Cheap: one `git diff --name-only` per distinct
anchor commit, cached across memories that share it.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import gitmeta
from .store import Store


def recompute(store: Store, root: Path) -> dict:
    """Re-evaluate staleness for every active memory. Returns summary counts."""
    if not gitmeta.is_repo(root):
        return {"checked": 0, "stale": 0, "cleared": 0}
    head = gitmeta._git(root, "rev-parse", "HEAD")
    if not head:
        return {"checked": 0, "stale": 0, "cleared": 0}

    rows = store.db.execute(
        "SELECT id, files, commit_range, stale FROM memory WHERE status='active'"
    ).fetchall()

    changed_cache: dict[str, set[str]] = {}
    now = time.time()
    checked = stale = cleared = 0

    for r in rows:
        import json
        files = set(json.loads(r["files"] or "[]"))
        anchor = (r["commit_range"] or "").strip()
        if not files or not anchor:
            continue
        checked += 1
        if anchor not in changed_cache:
            changed_cache[anchor] = _changed_since(root, anchor, head)
        touched = files & changed_cache[anchor]
        is_stale = bool(touched)
        was_stale = bool(r["stale"])
        if is_stale and not was_stale:
            store.set_stale(r["id"], True, now, sorted(touched))
            stale += 1
        elif is_stale:
            store.set_stale(r["id"], True, None, sorted(touched))  # refresh files
            stale += 1
        elif was_stale:
            store.set_stale(r["id"], False, None, [])
            cleared += 1
    store.db.commit()
    return {"checked": checked, "stale": stale, "cleared": cleared}


def _changed_since(root: Path, anchor: str, head: str) -> set[str]:
    """Files changed in anchor..head. Empty set if the anchor is unknown/invalid
    (a squashed/rebased-away sha) — we don't want to nuke every memory then."""
    if not gitmeta._git(root, "rev-parse", "--verify", "--quiet", anchor + "^{commit}"):
        return set()
    out = gitmeta._git(root, "diff", "--name-only", f"{anchor}..{head}")
    return {line.strip() for line in out.splitlines() if line.strip()}
