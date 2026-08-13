"""Team memory sharing over git — commit per-author shards, sync on session start.

The design is git-native and conflict-free. Each teammate exports *their own*
memories to a committed, per-author file under `.omni-memory/team/<author>.json`.
Because every author owns a distinct file, git merges cleanly — no conflicts on a
shared blob — and a `git pull` brings the team's memory into your store on the next
session, imported idempotently by id.

Trust: imported memories are tagged `source="shared"` (rendered ↗external, not
trusted as your own truth). But because memory is git-anchored by blob SHA — a
content hash identical across clones — a teammate's memory re-verifies on your
checkout, so it can re-earn `verified` locally via the normal graduation pass
rather than being trusted on faith.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import gitmeta
from .store import Store


def _slug(author: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (author or "").lower()).strip("-")
    return s[:48] or "unknown"


def team_dir(root: Path, create: bool = False) -> Path:
    d = root / ".omni-memory" / "team"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def enabled(root: Path) -> bool:
    """Team sharing is active once the committed team/ dir exists (opt-in via
    `omni-memory share`), so solo users are never bothered with shard files."""
    return team_dir(root).is_dir()


def write_shard(store: Store, root: Path) -> Optional[Path]:
    """Write THIS author's active memories to their committed shard. Returns the
    path, or None if there's no git identity to author under."""
    me = gitmeta.git_user(root)
    if not me:
        return None
    mine = [m for m in store.memories(status="active", limit=1_000_000)
            if (m.get("author") or "") == me]
    d = team_dir(root, create=True)
    path = d / f"{_slug(me)}.json"
    from .store import atomic_write
    atomic_write(path, json.dumps(
        {"omni_memory_export": 1, "author": me, "memories": mine}, indent=2))
    return path


def sync(store: Store, root: Path) -> int:
    """Import every teammate's shard (idempotent by id). Skips your own shard —
    those memories are already local. Returns the number newly added."""
    d = team_dir(root)
    if not d.is_dir():
        return 0
    me_slug = _slug(gitmeta.git_user(root))
    added = 0
    for f in sorted(d.glob("*.json")):
        if f.stem == me_slug:
            continue                              # my own memories are already here
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        added += store.import_memories(data, source="shared")
    return added


def status(store: Store, root: Path) -> dict:
    """Per-author counts for `omni-memory team`."""
    rows = store.db.execute(
        "SELECT COALESCE(NULLIF(author,''),'(unknown)') a, COUNT(*) c "
        "FROM memory WHERE status='active' GROUP BY a ORDER BY c DESC").fetchall()
    shards = sorted(p.name for p in team_dir(root).glob("*.json")) if enabled(root) else []
    return {"by_author": [dict(r) for r in rows], "shards": shards,
            "me": gitmeta.git_user(root), "enabled": enabled(root)}
