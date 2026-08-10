"""Staleness anchoring — flag memories whose code changed underneath them.

A memory is anchored to the commit it was written at (`commit_range`) and the
files/symbols it describes. When those change in later commits, the memory may no
longer be true. Rather than walk an AST at query time, we walk git history.

Two levels of precision, chosen automatically:
  - SYMBOL level (when a code graph exists): map the memory to its symbol(s),
    find which symbols actually changed between its anchor and HEAD (git diff
    hunk lines ∩ symbol line ranges), propagate to dependents via the call graph
    (affected.py), and flag the memory if its symbol is in that set. Precise —
    an unrelated edit to the same file does NOT flag it.
  - FILE level (fallback): flag if any of the memory's files changed at all.

Flagged memories keep their content (code changing doesn't disprove a decision)
but are marked so inject/recall/dashboard can show "⚠ may be stale, re-verify".
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import gitmeta
from .store import Store

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def recompute(store: Store, root: Path) -> dict:
    """Re-evaluate staleness for every active memory. Returns summary counts."""
    if not gitmeta.is_repo(root):
        return {"checked": 0, "stale": 0, "cleared": 0, "level": "none"}
    head = gitmeta._git(root, "rev-parse", "HEAD")
    if not head:
        return {"checked": 0, "stale": 0, "cleared": 0, "level": "none"}

    use_symbols = store.has_code_graph()
    nodes, edges = store.code_graph() if use_symbols else ([], [])
    file_symbols: dict[str, list[dict]] = {}
    name_index: dict[tuple, str] = {}
    for n in nodes:
        if n["kind"] in ("function", "method", "class"):
            file_symbols.setdefault(n["file"], []).append(n)
            name_index[(n["file"], n["name"])] = n["id"]

    rows = store.db.execute(
        "SELECT id, files, symbols, commit_range, stale FROM memory "
        "WHERE status='active'").fetchall()

    changed_files_cache: dict[str, set[str]] = {}
    affected_cache: dict[str, set[str]] = {}
    now = time.time()
    checked = stale = cleared = 0

    for r in rows:
        files = set(json.loads(r["files"] or "[]"))
        symbols = json.loads(r["symbols"] or "[]")
        anchor = (r["commit_range"] or "").strip()
        if not files or not anchor:
            continue
        checked += 1

        if anchor not in changed_files_cache:
            changed_files_cache[anchor] = _changed_files(root, anchor, head)
        changed_files = changed_files_cache[anchor]

        # Resolve this memory's own symbol ids (name seen in one of its files).
        mem_ids = {name_index[(f, name)] for f in files for name in symbols
                   if (f, name) in name_index} if use_symbols else set()

        if mem_ids:
            if anchor not in affected_cache:
                affected_cache[anchor] = _affected_symbols(
                    root, anchor, head, changed_files, file_symbols, edges)
            is_stale = bool(mem_ids & affected_cache[anchor])
            touched = sorted(mem_ids & affected_cache[anchor])
        else:  # file-level fallback
            hit = files & changed_files
            is_stale = bool(hit)
            touched = sorted(hit)

        was_stale = bool(r["stale"])
        if is_stale:
            store.set_stale(r["id"], True, None if was_stale else now, touched)
            stale += 1
        elif was_stale:
            store.set_stale(r["id"], False, None, [])
            cleared += 1
    store.db.commit()
    return {"checked": checked, "stale": stale, "cleared": cleared,
            "level": "symbol" if use_symbols else "file"}


def reconcile(store: Store, root: Path) -> dict:
    """Enumerate the SOURCE and diff it against memory to catch ORPHANS — memories
    whose anchored file (or symbol) was DELETED at the source.

    This is the half staleness-by-diff can't self-heal: a change re-flags itself
    on the next pass that touches the file, but a deletion emits exactly one event
    and nothing later mentions it, so an index-side query never notices what's
    missing. Because OmniMemory anchors memories to concrete git-tracked files and
    code-graph symbols, we can enumerate the live source and mark anything that no
    longer resolves. Orphans are flagged stale (→ eviction candidates)."""
    if not gitmeta.is_repo(root):
        return {"orphaned": 0, "coverage": 0.0, "anchored": 0}
    from .graph import extract
    live_files = {str(p.relative_to(root)) for p in extract._source_files(root)
                  if p.is_file()}
    sym_names = {n["name"] for n in store.code_graph()[0]
                 if n["kind"] in ("function", "method", "class")}
    now = time.time()
    orphaned = anchored = refetchable = 0
    for r in store.db.execute(
            "SELECT id, files, symbols, stale FROM memory WHERE status='active'"):
        files = json.loads(r["files"] or "[]")
        symbols = json.loads(r["symbols"] or "[]")
        if not files and not symbols:
            continue                              # unanchored — can't reconcile
        anchored += 1
        file_alive = any(f in live_files or (root / f).exists() for f in files)
        sym_alive = any(s.split("::")[-1].split(".")[-1] in sym_names for s in symbols)
        if file_alive or sym_alive:
            refetchable += 1
        else:                                     # every anchor gone → orphaned
            orphaned += 1
            store.set_stale(r["id"], True, None if r["stale"] else now, ["<deleted>"])
    store.db.commit()
    coverage = (refetchable / anchored) if anchored else 0.0
    return {"orphaned": orphaned, "coverage": round(coverage, 3), "anchored": anchored}


def _affected_symbols(root: Path, anchor: str, head: str, changed_files: set,
                      file_symbols: dict, edges: list) -> set:
    """Symbols that changed between anchor..head, plus their dependents."""
    from .graph import affected as aff
    changed: set[str] = set()
    for f in changed_files:
        syms = file_symbols.get(f)
        if not syms:
            continue
        lines = _changed_lines(root, anchor, head, f)
        if not lines:
            continue
        for s in syms:
            lo, hi = s["line_start"], s["line_end"]
            if any(lo <= ln <= hi for ln in lines):
                changed.add(s["id"])
    return aff.affected(changed, edges, depth=2)


def _changed_files(root: Path, anchor: str, head: str) -> set:
    if not gitmeta._git(root, "rev-parse", "--verify", "--quiet", anchor + "^{commit}"):
        return set()
    out = gitmeta._git(root, "diff", "--name-only", f"{anchor}..{head}")
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def _changed_lines(root: Path, anchor: str, head: str, file: str) -> set:
    """HEAD-side line numbers changed in `file` across anchor..head."""
    out = gitmeta._git(root, "diff", "--unified=0", f"{anchor}..{head}", "--", file)
    lines: set[int] = set()
    for row in out.splitlines():
        m = _HUNK.match(row)
        if not m:
            continue
        start = int(m.group(1))
        count = int(m.group(2)) if m.group(2) is not None else 1
        for ln in range(start, start + max(count, 1)):
            lines.add(ln)
    return lines
