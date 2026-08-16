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


_EMPTY_RECONCILE = {"orphaned": 0, "drifted": 0, "fresh": 0, "uncheckable": 0,
                    "anchored": 0, "coverage": 0.0, "locator_coverage": 0.0,
                    "refetch_coverage": 0.0, "source_enumeration_coverage": 0.0,
                    "observation_binding_coverage": 0.0, "unbound": 0,
                    "not_bindable": 0}


def _integrity_verdict(root: Path, shas: dict) -> str:
    """Exact source-integrity of one memory's anchored files, by re-resolving each
    recorded blob sha at HEAD. Worst anchor wins:
        ORPHANED (an object is gone) > DRIFTED (an sha changed) > FRESH.
    This is content identity, not a diff — no false positives from unrelated edits,
    and it catches deletion, which a diff against the index never can."""
    worst = "FRESH"
    for path, sha in shas.items():
        cur = gitmeta.blob_sha(root, path)
        if not cur:
            return "ORPHANED"                     # object missing at HEAD → deleted
        if cur != sha:
            worst = "DRIFTED"
    return worst


def reconcile(store: Store, root: Path) -> dict:
    """Measure source integrity by re-resolving each memory's recorded blob shas,
    and flag deletions. This is the half staleness-by-diff can't self-heal: an edit
    re-flags itself on the next pass, but a deletion emits one event and nothing
    later mentions it, so an index-side query never notices what's missing.

    Verdicts per anchored memory: FRESH (sha matches), DRIFTED (whole-file content
    changed), ORPHANED (source deleted), or UNCHECKABLE (legacy memory with no
    recorded blob sha — its path may resolve by name, but there's no content key to
    verify against, so it is *counted*, never claimed as re-fetchable).

    Only ORPHANED is flagged stale here: deletion is exact and index-side. DRIFT is
    left to symbol-level `recompute`, which is finer than whole-file — flagging drift
    here too would re-flag a memory for an unrelated edit elsewhere in its file.
    Returns measured coverage, reported honestly by the doctor."""
    if not gitmeta.is_repo(root):
        return dict(_EMPTY_RECONCILE)
    from .graph import extract
    live_files = {str(p.relative_to(root)) for p in extract._source_files(root)
                  if p.is_file()}
    sym_names = {n["name"] for n in store.code_graph()[0]
                 if n["kind"] in ("function", "method", "class")}
    now = time.time()
    anchored = fresh = drifted = orphaned = uncheckable = with_locator = with_files = 0
    observed_n = unbound_n = not_bindable = 0
    for r in store.db.execute(
            "SELECT id, files, symbols, blob_shas, stale, observed, unbound FROM memory "
            "WHERE status='active'"):
        files = json.loads(r["files"] or "[]")
        symbols = json.loads(r["symbols"] or "[]")
        if not files and not symbols:
            # No content-addressable anchor: binding is IMPOSSIBLE, not missing. Count
            # it BESIDE the denominator (never inside), so coverage isn't held below
            # 100% forever by a class that can never be backfilled.
            not_bindable += 1
            continue
        anchored += 1
        observed_n += 1 if r["observed"] else 0   # bound to bytes actually READ (vs declared)
        unbound_n += 1 if r["unbound"] else 0     # source moved between read and capture
        if files:
            with_files += 1                       # deletion-enumerable via the git tree
        shas = json.loads(r["blob_shas"] or "{}")
        if shas:                                  # exact, content-identity path
            with_locator += 1
            verdict = _integrity_verdict(root, shas)
            if verdict == "ORPHANED":
                orphaned += 1
                store.set_stale(r["id"], True, None if r["stale"] else now, ["<deleted>"])
            elif verdict == "DRIFTED":
                drifted += 1                      # counted; recompute owns staleness
            else:
                fresh += 1
            continue
        # legacy memory (no blob sha): resolve by name only — can't verify content
        file_alive = any(f in live_files or (root / f).exists() for f in files)
        sym_alive = any(s.split("::")[-1].split(".")[-1] in sym_names for s in symbols)
        if file_alive or sym_alive:
            uncheckable += 1
        else:                                     # every anchor gone → orphaned
            orphaned += 1
            store.set_stale(r["id"], True, None if r["stale"] else now, ["<deleted>"])
    store.db.commit()
    # locator_coverage: fraction carrying a re-fetchable content key (schema).
    # refetch_coverage: fraction we verified re-fetchable TODAY (fresh present) —
    # the measured number; it drops below locator_coverage exactly when sources drift
    # or vanish. Legacy 'uncheckable' memories count toward neither.
    loc = (with_locator / anchored) if anchored else 0.0
    ref = (fresh / anchored) if anchored else 0.0
    # source_enumeration_coverage: fraction whose deletion is detectable by
    # enumerating the authoritative source. We enumerate the git tree (`git
    # ls-files`), so any file-anchored memory's disappearance is catchable — this
    # is the "orphan slice" that's real regardless of retrieval.
    enum = (with_files / anchored) if anchored else 0.0
    # observation_binding_coverage: a SEPARATE axis from refetchability — the fraction
    # bound to the bytes the agent actually READ (read-ledger), not just the file at
    # capture. "declared" is load-bearing: only the reader knows what it read, so a
    # memory with no read record is declared, not observed. `unbound` counts the
    # UNBOUND_CAPTURE slice: the source moved between observation and capture.
    obs = (observed_n / anchored) if anchored else 0.0
    return {"orphaned": orphaned, "drifted": drifted, "fresh": fresh,
            "uncheckable": uncheckable, "anchored": anchored,
            "coverage": round(ref, 3), "locator_coverage": round(loc, 3),
            "refetch_coverage": round(ref, 3),
            "source_enumeration_coverage": round(enum, 3),
            "observation_binding_coverage": round(obs, 3), "unbound": unbound_n,
            "not_bindable": not_bindable}


def graduate_verified(store: Store, root: Path) -> int:
    """Promote a memory to `verified` only when the LIBRARY can warrant it — the
    single legitimate path to the top tier for machine-captured memory. A memory
    graduates when its source anchor is re-fetchable AND unchanged (every recorded
    blob sha still matches HEAD) AND the agent has cited it (uses>0). Content can
    never self-declare `verified`; it must be earned by outcome + reality."""
    if not gitmeta.is_repo(root):
        return 0
    n = 0
    for r in store.db.execute(
            "SELECT id, blob_shas, source FROM memory WHERE status='active' "
            "AND evidence='stated' AND COALESCE(uses,0) > 0"):
        shas = json.loads(r["blob_shas"] or "{}")
        if not shas or r["source"] in ("imported", "shared"):
            continue                              # no content key / untrusted origin
        if _integrity_verdict(root, shas) == "FRESH":
            store.db.execute("UPDATE memory SET evidence='verified' WHERE id=?", (r["id"],))
            n += 1
    if n:
        store.db.commit()
    return n


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
