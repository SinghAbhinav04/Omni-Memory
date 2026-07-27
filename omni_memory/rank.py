"""Relevance ranking for memory retrieval — zero-dep, pure Python + math.

The old path filtered with SQL `LIKE` and required *every* query token to be
present, then sorted by recency. That buries a memory that matches 2 of 3 terms
and lets stale-but-recent noise win. This replaces it with an IDF-weighted,
tiered scorer:

  score = (Σ per-term tier·IDF) · coverage²        # relevance core
        + full-query bonus                          # multi-word phrase match
        + file/symbol overlap boost                 # anchored to current work
        × recency · kind-weight · confidence        # memory-specific signals

No external search dependency.
"""
from __future__ import annotations

import math
import re
import time
from typing import Optional

# Match tiers: exact ≫ prefix > substring, taken strongest-per-term.
_EXACT, _PREFIX, _SUBSTR = 10.0, 3.0, 1.0
_FULLQ_EXACT, _FULLQ_PREFIX = 6.0, 2.0
_FILE_BOOST = 4.0            # memory anchored to a file in play right now
_HALF_LIFE_DAYS = 45.0      # recency decay: a memory is worth ~½ after this

# Durable, high-signal kinds get a nudge up; speculative ones a nudge down.
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


def _mem_text(m: dict) -> str:
    """Everything a query can legitimately match against for one memory."""
    parts = [m.get("text", ""), m.get("kind", "")]
    parts += m.get("files", []) or []
    parts += m.get("symbols", []) or []
    return " ".join(parts)


def _idf(corpus_tokens: list[list[str]], terms: list[str]) -> dict[str, float]:
    """Inverse doc frequency over the candidate set — rare terms score higher."""
    n = len(corpus_tokens) or 1
    df: dict[str, int] = {}
    for toks in corpus_tokens:
        for t in set(toks):
            if t in terms:
                df[t] = df.get(t, 0) + 1
    return {t: math.log(1 + n / (1 + df.get(t, 0))) for t in terms}


def _term_score(term: str, w: float, mtoks: set[str], mtext: str) -> tuple[float, bool]:
    """Strongest tier for one query term against one memory. Returns (score, matched)."""
    if term in mtoks:
        return _EXACT * w, True
    for mt in mtoks:                      # prefix: query term begins a memory token
        if mt.startswith(term) or term.startswith(mt):
            return _PREFIX * w, True
    if term in mtext:                     # loose substring fallback
        return _SUBSTR * w, True
    return 0.0, False


def score_one(m: dict, terms: list[str], idf: dict[str, float],
              joined: str, files: Optional[set[str]], now: float) -> float:
    mtext = _mem_text(m).lower()
    mtoks = set(tokens(mtext))
    tiered, matched = 0.0, 0
    for t in terms:
        s, hit = _term_score(t, idf.get(t, 1.0), mtoks, mtext)
        tiered += s
        matched += hit
    if matched == 0 and not (files and files & set(m.get("files", []))):
        return 0.0
    # coverage²: matching many of the query's terms beats a lone generic hit.
    coverage = (matched / len(terms)) ** 2 if terms else 1.0
    score = tiered * coverage

    if joined and len(terms) > 1:         # whole-phrase bonus, weighted by rarest term
        w = max((idf.get(t, 1.0) for t in terms), default=1.0)
        if joined in mtext:
            score += _FULLQ_EXACT * w
        elif any(joined in x for x in (mtext,)):
            score += _FULLQ_PREFIX * w

    if files and files & set(m.get("files", [])):   # anchored to files in play
        score += _FILE_BOOST

    # recency (exponential half-life) · kind weight · stored confidence
    age_days = max(0.0, (now - float(m.get("updated") or now)) / 86400.0)
    recency = 0.5 ** (age_days / _HALF_LIFE_DAYS)
    score *= (0.6 + 0.4 * recency)        # recency modulates, never zeroes
    score *= _KIND_WEIGHT.get(m.get("kind", "fact"), 1.0)
    score *= 0.5 + 0.5 * float(m.get("confidence") or 0.8)
    return score


def rank(mems: list[dict], query: str, files: Optional[list[str]] = None,
         now: Optional[float] = None) -> list[dict]:
    """Return memories ranked by relevance to `query` (+ files in play).

    Memories that match nothing are dropped, so an injected block stays on-topic.
    With no query, order by recency (graceful fallback for browse/digest paths).
    """
    now = now or time.time()
    fileset = set(files) if files else None
    terms = list(dict.fromkeys(tokens(query)))
    if not terms and not fileset:
        return sorted(mems, key=lambda m: m.get("updated") or 0, reverse=True)
    joined = " ".join(terms)
    idf = _idf([tokens(_mem_text(m)) for m in mems], terms)
    scored = [(score_one(m, terms, idf, joined, fileset, now), m) for m in mems]
    hits = [(s, m) for s, m in scored if s > 0]
    hits.sort(key=lambda sm: (sm[0], sm[1].get("updated") or 0), reverse=True)
    return [m for _, m in hits]
