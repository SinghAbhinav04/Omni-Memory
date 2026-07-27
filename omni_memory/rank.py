"""Relevance ranking for memory retrieval — zero-dep, pure Python + math.

Scores a candidate memory against a query, the files/symbols in play, and how
useful the memory has proven before:

  BM25F lexical core   — per-field term matching (symbols ≫ files > text > kind),
                         tf saturation + length normalization, weighted by IDF
  · coverage²          — matching many query terms beats a lone generic hit
  + full-phrase bonus  — multi-word phrase match
  + file-in-play boost — memory anchored to a file you're editing
  + graph proximity    — memory anchored to a symbol near your edit (call graph)
  × recency            — exponential half-life
  × kind · confidence  — durable kinds and confident facts rank up
  × usage              — memories the agent actually cites float up over time

No external search dependency.
"""
from __future__ import annotations

import math
import re
import time
from typing import Optional

# BM25F field weights: a query term matching a symbol name is far stronger signal
# than one buried in prose.
_FIELD_W = {"symbols": 3.0, "files": 2.0, "text": 1.0, "kind": 0.5}
# Relative match quality within a field: exact ≫ prefix > substring.
_EXACT, _PREFIX, _SUBSTR = 1.0, 0.45, 0.15
_K1 = 1.2          # tf saturation
_B = 0.30          # length-normalization strength (mild — memories are short)

_FULLQ_EXACT, _FULLQ_PREFIX = 6.0, 2.0
_FILE_BOOST = 4.0            # memory anchored to a file in play right now
_GRAPH_BOOST = 5.0          # memory anchored to a symbol near the current edit
_USE_W = 0.30               # how much citation history lifts a memory
_HALF_LIFE_DAYS = 45.0

_KIND_WEIGHT = {
    "decision": 1.25, "gotcha": 1.2, "event": 1.15, "endpoint": 1.15,
    "db": 1.1, "flow": 1.1, "component": 1.05, "concept": 1.05,
    "fact": 1.0, "todo": 0.9, "assumption": 0.8,
}

_STOP = frozenset(
    "a an the and or of to in on for with is are be this that it its as at by "
    "from how does do we our i you it what when where which who why can use "
    "using used into via not no yes".split())
_TOKEN = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> list[str]:
    """Lowercase alnum/underscore tokens, stopwords + 1-char tokens dropped."""
    return [t for t in _TOKEN.findall((text or "").lower())
            if len(t) > 1 and t not in _STOP]


def _fields(m: dict) -> dict[str, str]:
    return {"symbols": " ".join(m.get("symbols") or []),
            "files": " ".join(m.get("files") or []),
            "text": m.get("text", ""),
            "kind": m.get("kind", "")}


def _mem_text(m: dict) -> str:
    f = _fields(m)
    return " ".join(f.values())


def _idf(corpus_tokens: list[list[str]], terms: list[str]) -> dict[str, float]:
    """Inverse doc frequency over the candidate set — rare terms score higher."""
    n = len(corpus_tokens) or 1
    df: dict[str, int] = {}
    for toks in corpus_tokens:
        for t in set(toks):
            if t in terms:
                df[t] = df.get(t, 0) + 1
    return {t: math.log(1 + n / (1 + df.get(t, 0))) for t in terms}


def _field_tier(term: str, ftoks: set, ftext: str) -> float:
    """Best relative match quality for one term against one field."""
    if term in ftoks:
        return _EXACT
    for t in ftoks:
        if t.startswith(term) or term.startswith(t):
            return _PREFIX
    if term in ftext:
        return _SUBSTR
    return 0.0


def _saturate(x: float) -> float:
    return x * (_K1 + 1) / (x + _K1) if x > 0 else 0.0


def score_one(m: dict, terms: list[str], idf: dict, joined: str,
              files: Optional[set], now: float, context: Optional[dict],
              avg_len: float) -> float:
    fields = _fields(m)
    ftoks = {f: set(tokens(v)) for f, v in fields.items()}
    ftext = {f: v.lower() for f, v in fields.items()}
    dl = max(1, len(ftoks["text"]))
    lengthnorm = 1.0 / (1 - _B + _B * dl / max(avg_len, 1.0))

    tiered, matched = 0.0, 0
    for t in terms:
        raw = 0.0
        for f, w in _FIELD_W.items():
            q = _field_tier(t, ftoks[f], ftext[f])
            if q:
                raw += w * q * (lengthnorm if f == "text" else 1.0)
        if raw > 0:
            matched += 1
            tiered += idf.get(t, 1.0) * _saturate(raw)

    graph = 0.0
    if context and m.get("symbols"):
        graph = max((context.get(s, 0.0) for s in m["symbols"]), default=0.0)
    file_hit = bool(files and files & set(m.get("files", [])))
    if matched == 0 and not file_hit and graph == 0.0:
        return 0.0

    coverage = (matched / len(terms)) ** 2 if terms else 1.0
    score = tiered * coverage

    if joined and len(terms) > 1:  # whole-phrase bonus, weighted by rarest term
        w = max((idf.get(t, 1.0) for t in terms), default=1.0)
        if joined in ftext["text"]:
            score += _FULLQ_EXACT * w
        elif any(joined in ftext[f] for f in ("symbols", "files")):
            score += _FULLQ_PREFIX * w

    if file_hit:
        score += _FILE_BOOST
    if graph:
        score += _GRAPH_BOOST * graph

    age_days = max(0.0, (now - float(m.get("updated") or now)) / 86400.0)
    recency = 0.5 ** (age_days / _HALF_LIFE_DAYS)
    score *= (0.6 + 0.4 * recency)
    score *= _KIND_WEIGHT.get(m.get("kind", "fact"), 1.0)
    score *= 0.5 + 0.5 * float(m.get("confidence") or 0.8)
    score *= 1.0 + _USE_W * math.log(1 + (m.get("uses") or 0))  # citation feedback
    return score


def rank(mems: list[dict], query: str, files: Optional[list[str]] = None,
         now: Optional[float] = None, context: Optional[dict] = None) -> list[dict]:
    """Return memories ranked by relevance to `query`, the files/symbols in play,
    and citation history. Memories matching nothing are dropped when there's a
    query; with no query, proximity + recency just reorder (nothing dropped)."""
    now = now or time.time()
    fileset = set(files) if files else None
    terms = list(dict.fromkeys(tokens(query)))

    if not terms:  # browse/context path: reorder by proximity + recency, keep all
        if not fileset and not context:
            return sorted(mems, key=lambda m: m.get("updated") or 0, reverse=True)

        def prox(m):
            g = max((context.get(s, 0.0) for s in (m.get("symbols") or [])),
                    default=0.0) if context else 0.0
            fh = 1.0 if fileset and fileset & set(m.get("files", [])) else 0.0
            return (_GRAPH_BOOST * g + _FILE_BOOST * fh, m.get("updated") or 0)
        return sorted(mems, key=prox, reverse=True)

    joined = " ".join(terms)
    idf = _idf([tokens(_mem_text(m)) for m in mems], terms)
    text_lens = [len(tokens(m.get("text", ""))) for m in mems]
    avg_len = (sum(text_lens) / len(text_lens)) if text_lens else 1.0
    scored = [(score_one(m, terms, idf, joined, fileset, now, context, avg_len), m)
              for m in mems]
    hits = [(s, m) for s, m in scored if s > 0]
    hits.sort(key=lambda sm: (sm[0], sm[1].get("updated") or 0), reverse=True)
    return [m for _, m in hits]
