"""Inject + enforce — build the VERIFIED PROJECT MEMORY block for a prompt.

This is what makes the agent *use* memory instead of hallucinating: relevant
memories (scoped to the current branch + base) are injected with hard rules to
cite what was used and to admit when something isn't in memory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import branch as branchmod
from .store import Store

ENFORCE_RULES = (
    "RULES: (1) Treat the memory below as verified project truth — prefer it over "
    "assumptions. (2) When you rely on a memory, cite its [id]. (3) If the answer "
    "is NOT covered by memory or the code, say \"not in memory\" — do NOT invent "
    "architecture, endpoints, params, or flows."
)


def build_block(store: Store, root: Path, query: str = "",
                files: Optional[list[str]] = None, limit: int = 20) -> str:
    cur, base = branchmod.scope(store, root)
    branch = None if cur == "*" else cur
    mems = store.memories(branch=branch, base=base, files=files, query=query,
                          limit=limit)
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
        lines.append(f"{tag} {m['kind']}{br}: {m['text']}{where}")
    lines.append("=== END MEMORY — cite [id]s you used ===")
    return "\n".join(lines)
