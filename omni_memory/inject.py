"""Inject + enforce — build the VERIFIED PROJECT MEMORY block for a prompt.

This is what makes the agent *use* memory instead of hallucinating: relevant
memories (scoped to the current branch + base) are injected with hard rules to
cite what was used and to admit when something isn't in memory.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import branch as branchmod, gitmeta
from .store import Store

_PATH_RE = re.compile(r"[\w./-]+\.[A-Za-z0-9]{1,5}")


def _files_in_play(root: Path, query: str, files: Optional[list[str]]) -> list[str]:
    """What the agent is working on: explicit files + file paths named in the
    prompt + files with uncommitted changes. Drives graph-proximity ranking."""
    fip = set(files or [])
    fip.update(m for m in _PATH_RE.findall(query or "") if "/" in m or "." in m)
    changed = gitmeta._git(root, "diff", "--name-only", "HEAD")
    fip.update(ln.strip() for ln in changed.splitlines() if ln.strip())
    return [f for f in fip if f]

ENFORCE_RULES = (
    "RULES: (1) Treat the memory below as verified project truth — prefer it over "
    "assumptions. (2) When you rely on a memory, cite its [id]. (3) If the answer "
    "is NOT covered by memory or the code, say \"not in memory\" — do NOT invent "
    "architecture, endpoints, params, or flows. (4) Items marked ⚠STALE reference "
    "code that changed since they were written — re-verify against the code before "
    "relying on them."
)


def build_block(store: Store, root: Path, query: str = "",
                files: Optional[list[str]] = None, limit: int = 20) -> str:
    cur, base = branchmod.scope(store, root)
    branch = None if cur == "*" else cur
    fip = _files_in_play(root, query, files)
    from .graph import proximity
    context = proximity.context_from_files(store, fip)
    mems = store.memories(branch=branch, base=base, files=fip or None,
                          query=query, context=context, limit=limit)
    if not mems and query:  # widen if the query filter was too tight
        mems = store.memories(branch=branch, base=base, limit=limit)
    if not mems:
        return ""
    scope_label = "all branches" if cur == "*" else (
        f"branch '{cur}'" + (f" + base '{base}'" if base else ""))
    lines = [
        "=== VERIFIED PROJECT MEMORY (OmniMemory) ===",
        f"scope: {scope_label} · {len(mems)} item(s)",
        ENFORCE_RULES,
        "",
    ]
    for m in mems:
        tag = f"[{m['id']}]"
        where = (" · " + ", ".join(m["files"][:3])) if m["files"] else ""
        br = "" if cur != "*" else f" ({m['branch']})"
        stale = " ⚠STALE" if m.get("stale") else ""
        lines.append(f"{tag} {m['kind']}{br}{stale}: {m['text']}{where}")
    lines.append("=== END MEMORY — cite [id]s you used ===")
    return "\n".join(lines)
