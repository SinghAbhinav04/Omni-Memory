"""Extraction-noise filter — drop junk before it becomes a memory.

An LLM (or the doc/heuristic scanner) happily emits aspirational prose, headings,
and doc boilerplate that pollute the graph and the inject block. A *durable*
project memory names something concrete — a file, symbol, path, endpoint, table,
CamelCase type, or quoted term. Generic prose with no such anchor is noise.

Applied in `remember_many` for every capture path. Filtering is source-aware:
  - ALL sources    → length bounds + structural (markdown/heading/question) reject
  - doc/heuristic  → additionally require a concrete anchor (these scanners are
                     the noisy ones; the AI extractor is trusted for prose).
"""
from __future__ import annotations

import re

_MIN_CHARS = 12
# Storage-worthiness is NOT the same as injection-size: injection truncates
# independently (inject._TEXT_CAP), so the store can safely keep a longer, dense
# memory. We only reject candidates that are so long they're clearly a dumped
# paragraph rather than one atomic fact — by word count (the real signal) with a
# generous char ceiling as a backstop.
_MAX_CHARS = 600
_MAX_WORDS = 60
_PROSE_WORDS = 8  # a label with >= this many words reads like prose, not a name

# Sources whose output is untrusted prose and must carry a concrete anchor.
_STRICT_SOURCES = {"doc", "heuristic"}

# A memory is "anchored" if it points at something concrete. Any of:
_PATH = re.compile(r"[\w-]+/[\w./-]+")                 # src/foo/bar.py
_DOTTED = re.compile(r"\b\w+\.\w+")                    # module.func, Foo.bar
_SNAKE = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")       # snake_case
_CAMEL = re.compile(r"\b[a-z]+[A-Z]\w+\b")             # camelCase
_TYPE = re.compile(r"\b[A-Z][a-z0-9]+[A-Z]\w+\b")     # CamelCase / PascalCase
_ALLCAPS = re.compile(r"\b[A-Z]{2,}\b")               # KAFKA, HTTP, DB, API
_ENDPOINT = re.compile(r"(/api/|\b(GET|POST|PUT|PATCH|DELETE)\b|:\d{2,5}\b)")
_QUOTED = re.compile(r"[\"'`][^\"'`]{2,}[\"'`]")       # "quoted" term
# A capitalized proper noun that is NOT at the start of the sentence.
_MIDCAP = re.compile(r"\S\s+[A-Z][a-zA-Z0-9]{2,}")

_ANCHORS = (_PATH, _DOTTED, _SNAKE, _CAMEL, _TYPE, _ALLCAPS, _ENDPOINT, _QUOTED,
            _MIDCAP)

# Boilerplate / doc-scaffolding fragments that are never durable facts.
_BOILERPLATE = re.compile(
    r"^(see |for more|e\.?g\.?|i\.?e\.?|note:|todo:?$|tbd|n/a|coming soon"
    r"|as follows|for example|in this|this (section|doc|file|guide))",
    re.I,
)


def _has_anchor(text: str) -> bool:
    return any(rx.search(text) for rx in _ANCHORS)


def is_noise(text: str, files=None, symbols=None, source: str = "session") -> bool:
    """True if this candidate memory should be dropped as extraction noise."""
    t = (text or "").strip()
    if len(t) < _MIN_CHARS or len(t) > _MAX_CHARS or len(t.split()) > _MAX_WORDS:
        return True
    # structural markdown / doc scaffolding
    if t[0] in "#>|=" or t.startswith(("```", "---", "* ", "- ")) and len(t.split()) < 3:
        return True
    if t.endswith("?"):                       # questions aren't durable facts
        return True
    if _BOILERPLATE.match(t):
        return True
    if source not in _STRICT_SOURCES:
        return False                          # trust AI/manual/session prose
    # strict path: doc/heuristic prose must name something concrete
    if files or symbols:
        return False
    return not _has_anchor(t)


def filter_items(items: list[dict], source: str = "session") -> tuple[list[dict], int]:
    """Return (kept_items, dropped_count) after removing extraction noise."""
    kept, dropped = [], 0
    for it in items:
        text = (it.get("text") or "").strip()
        if is_noise(text, it.get("files"), it.get("symbols"), source):
            dropped += 1
            continue
        kept.append(it)
    return kept, dropped
