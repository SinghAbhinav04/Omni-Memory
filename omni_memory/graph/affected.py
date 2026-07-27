"""Impact analysis over the code graph — what depends on a changed symbol.

Given a set of symbols that changed (their source moved under them in git), walk
the call/inherit edges *in reverse* (callee -> caller) to find every symbol whose
behaviour may have shifted as a result. A memory anchored to any symbol in that
set is a candidate for going stale.

Pure data structures, zero-dep. Depth-bounded so a change to a hot utility
doesn't flag the entire repo.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

_DEFAULT_RELS = ("calls", "inherits")


def reverse_index(edges: list[dict], rels: Iterable[str] = _DEFAULT_RELS) -> dict:
    """dst -> [src]: for each symbol, who depends on it."""
    rels = set(rels)
    idx: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        if e["rel"] in rels:
            idx[e["dst"]].append(e["src"])
    return idx


def affected(changed: Iterable[str], edges: list[dict], *, depth: int = 2,
             rels: Iterable[str] = _DEFAULT_RELS) -> set[str]:
    """Changed symbols plus their transitive dependents, up to `depth` hops."""
    idx = reverse_index(edges, rels)
    seen: set[str] = set(changed)
    q: deque[tuple[str, int]] = deque((c, 0) for c in seen)
    while q:
        cur, d = q.popleft()
        if d >= depth:
            continue
        for dep in idx.get(cur, ()):
            if dep not in seen:
                seen.add(dep)
                q.append((dep, d + 1))
    return seen
