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

# Compressed to ~1/3 the length: it's injected on EVERY message, so every word
# is a recurring token cost. Same four directives, terse.
ENFORCE_RULES = (
    "Rules: treat these as verified project truth; cite the [id]s you use; if it's "
    "not here or in the code say \"not in memory\" (don't invent it); re-verify ⚠STALE items."
)


# Keep the per-prompt injection cheap: it rides on EVERY message, so it must be
# a tight, high-signal budget rather than a memory dump. Defaults are overridable
# per-project via meta keys so users can trade recall for tokens.
_MAX_ITEMS = 8           # hard cap on injected memories (was 10)
_WIDEN_ITEMS = 4         # when nothing matched, only a few top memories
_TEXT_CAP = 160          # truncate long memory text
_CHAR_BUDGET = 1400      # ~350 tokens for the whole block (was 1800)


def build_block(store: Store, root: Path, query: str = "",
                files: Optional[list[str]] = None, limit: int = _MAX_ITEMS) -> str:
    """Build the `VERIFIED PROJECT MEMORY` block injected into a prompt.

    Scopes to the current branch+base, ranks memories by relevance to the query
    and the files in play, then emits a *budget-capped* set with enforcement
    rules. Returns "" when there's nothing worth injecting. Kept deliberately
    lean because it rides on every message (see the _MAX_* / _CHAR_BUDGET caps)."""
    # per-project overrides (set via `omni-memory usage --max-items/--budget`)
    limit = int(store.get_meta("inject_max_items", limit))
    char_budget = int(store.get_meta("inject_char_budget", _CHAR_BUDGET))
    widen = int(store.get_meta("inject_widen_items", _WIDEN_ITEMS))
    cur, base = branchmod.scope(store, root)
    branch = None if cur == "*" else cur
    fip = _files_in_play(root, query, files)
    from .graph import proximity
    context = proximity.context_from_files(store, fip)
    mems = store.memories(branch=branch, base=base, files=fip or None,
                          query=query, context=context, limit=limit)
    widened = False
    if not mems and query:  # nothing matched: inject only a FEW top memories,
        widened = True      # not a full 20-item dump on an unrelated prompt
        mems = store.memories(branch=branch, base=base, limit=widen)
    # layer in a few cross-project (global) memories from ~/.omni-memory so the
    # user's standing knowledge/preferences travel into every project. Only if a
    # global store already exists — don't materialize one just by injecting.
    gitems = int(store.get_meta("inject_global_items", 3))
    from .store import global_dir
    if gitems > 0 and (global_dir() / "omni.db").exists():
        try:
            from .store import Store as _Store
            gs = _Store(exact_dir=global_dir())
            seen = {m["id"] for m in mems}
            for gm in gs.memories(query=query, limit=gitems):
                if gm["id"] not in seen:
                    gm["_global"] = True
                    mems.append(gm)
        except Exception:  # noqa: BLE001
            pass
    if not mems:
        return ""
    scope_label = "all branches" if cur == "*" else (
        f"branch '{cur}'" + (f" + base '{base}'" if base else ""))
    lines = [
        "=== VERIFIED PROJECT MEMORY (OmniMemory) ===",
        f"scope: {scope_label}" + (" · top general memories" if widened else ""),
        ENFORCE_RULES,
        "",
    ]
    used, budget, has_external, has_inferred = 0, char_budget, False, False
    for m in mems:
        tag = f"[{m['id']}]"
        where = (" · " + ", ".join(m["files"][:2])) if m["files"] else ""
        # provenance: global / externally-sourced (imported or a committed shared
        # file) memories are NOT this project's own capture — mark them so the
        # agent weights them accordingly instead of trusting them as ground truth.
        if m.get("_global"):
            br = " 🌐global"
            has_external = True
        elif m.get("source") in ("shared", "imported"):
            br = " ↗external"
            has_external = True
        else:
            br = "" if cur != "*" else f" ({m['branch']})"
        stale = " ⚠STALE" if m.get("stale") else ""
        # evidence: how it was learned. ✓ = confirmed by outcome (trust it);
        # ~ = inferred/unconfirmed (weigh lightly). stated → no marker.
        ev = {"verified": " ✓", "inferred": " ~"}.get(m.get("evidence"), "")
        if m.get("evidence") == "inferred":
            has_inferred = True
        text = m["text"] if len(m["text"]) <= _TEXT_CAP else m["text"][:_TEXT_CAP - 1] + "…"
        line = f"{tag}{ev} {m['kind']}{br}{stale}: {text}{where}"
        if used and budget - len(line) < 0:   # keep at least one; then honor budget
            break
        lines.append(line)
        budget -= len(line)
        used += 1
    if has_inferred:
        lines.insert(3, "Note: ✓ = verified by outcome (trust it); ~ = inferred/"
                        "unconfirmed (weigh lightly, re-verify before relying).")
    if has_external:
        lines.insert(3, "Note: ↗external / 🌐global items came from outside this "
                        "project's own capture (imported/shared/global) — verify "
                        "before trusting them as this project's truth.")
    lines[1] = lines[1].replace("scope:", f"scope: {used} item(s) ·", 1)
    lines.append("=== END MEMORY — cite [id]s you used ===")
    return "\n".join(lines)
