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
# A path needs an extension (`src/foo/bar.py`) or real depth (`a/b/c`). Plain
# `word/word` is not a path — English writes "commit/switch", "drift/delete", and
# "and/or", and accepting those let ordinary prose certify itself as anchored.
_PATH = re.compile(r"(?:[\w-]+/)+[\w.-]*\.[A-Za-z]\w{0,4}\b"
                   r"|(?:[\w-]+/){2,}[\w-]+")
_DOTTED = re.compile(r"\b\w+\.\w+")                    # module.func, Foo.bar
_SNAKE = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b")       # snake_case
_CAMEL = re.compile(r"\b[a-z]+[A-Z]\w+\b")             # camelCase
_TYPE = re.compile(r"\b[A-Z][a-z0-9]+[A-Z]\w+\b")     # CamelCase / PascalCase
_ALLCAPS = re.compile(r"\b[A-Z]{2,}\b")               # KAFKA, HTTP, DB, API
_ENDPOINT = re.compile(r"(/api/|\b(GET|POST|PUT|PATCH|DELETE)\b|:\d{2,5}\b)")
_QUOTED = re.compile(r"[\"'`][^\"'`]{2,}[\"'`]")       # "quoted" term

# A proper noun mid-sentence ("a Redis token-bucket", "backed by Postgres") does name
# something concrete, and it is often the ONLY anchor a decision carries. But the old
# rule — any capital preceded by any non-space — also fired on a new sentence after
# punctuation ("… today? Not the schema") and on common function words, which is how
# ordinary prose certified itself. So: require a lowercase word immediately before it,
# and exclude the function words that start most English sentences.
_MIDCAP = re.compile(
    r"[a-z]\s+(?!(?:The|This|That|These|Those|Not|It|We|You|They|If|But|And|Or|So|As|"
    r"In|On|At|To|For|An?|Its|Their|There|When|While|Then|Also|Only)\b)"
    r"[A-Z][a-zA-Z0-9]{2,}")

_ANCHORS = (_PATH, _DOTTED, _SNAKE, _CAMEL, _TYPE, _ALLCAPS, _ENDPOINT, _QUOTED,
            _MIDCAP)

# Boilerplate / doc-scaffolding fragments that are never durable facts.
_BOILERPLATE = re.compile(
    r"^(see |for more|e\.?g\.?|i\.?e\.?|note:|todo:?$|tbd|n/a|coming soon"
    r"|as follows|for example|in this|this (section|doc|file|guide))",
    re.I,
)

# Conversational / thread noise — a pasted chat log, GitHub issue comment, forum
# reply, or PR discussion is not project memory. These are rejected across ALL
# sources: the heuristic transcript scanner happily turns discussion chatter into
# "facts" (an issue thread became dozens of junk memories), and even an LLM can be
# fooled into extracting a quoted comment as a decision.
_CHAT = re.compile(
    r"^\s*(?:assistant|user|system|human)\s*:"        # a flattened transcript turn
    r"|\b(?:let me|i'll|i will|now (?:let's|implementing)|waiting on|"
    r"here's what|good (?:catch|question|challenge))\b"   # narration, not a fact
    r"|^@\w+"                                         # opens with a mention (a reply)
    r"|@\w+\s+(commented|said|wrote|replied|asked|mentioned)"
    r"|\b(commented|replied|posted|wrote)\b.{0,24}\b(ago|on \w+ \d)"
    r"|\b\d+\s*(h|hr|hrs|hours?|d|days?|mo|months?|w|weeks?|min|minutes?)\s+ago\b"
    r"|github\.com/\S+/(issues|pull|discussions)"
    r"|\bdrafted by\b|\bposted with\b|review and approval"
    r"|^https?://\S+$"                                # a bare link
    r"|^>\s"                                          # a quoted reply line
    r"|^@[\w-]+\b",                                   # opens with a mention = a reply
    re.I,
)


def _has_anchor(text: str) -> bool:
    return any(rx.search(text) for rx in _ANCHORS)


def _is_chatter(t: str) -> bool:
    """Structural markers of a conversation/thread rather than a project fact.

    A real thread reply OPENS with a mention (`@handle …`) or pairs a mention with
    a speech verb (`@handle commented`); a code fact carries decorators/annotations
    (`@Retryable`, `@app.route`, `@pytest.fixture`) mid-sentence. Matching a bare
    count of `@tokens` conflated the two and silently dropped framework-wiring
    memories — so we key only on the structural markers in `_CHAT`."""
    return bool(_CHAT.search(t))


def is_noise(text: str, files=None, symbols=None, source: str = "session") -> bool:
    """True if this candidate memory should be dropped as extraction noise."""
    t = (text or "").strip()
    if len(t) < _MIN_CHARS or len(t) > _MAX_CHARS or len(t.split()) > _MAX_WORDS:
        return True
    # structural markdown / doc scaffolding. `and` binds tighter than `or`, so the
    # original read as `A or (B and C)` — a fenced block or an `---` rule survived at
    # any length above three words. Fences and rules are never facts; bullets and
    # headings only get the short-line test.
    if t.startswith(("```", "---", "===", "|")):
        return True
    if t[0] in "#>|=" or t.startswith(("* ", "- ")):
        if len(t.split()) < 3:
            return True
    if t.endswith("?"):                       # questions aren't durable facts
        return True
    if _BOILERPLATE.match(t):
        return True
    if _is_chatter(t):                        # pasted thread / issue-comment noise
        return True
    if source not in _STRICT_SOURCES:
        return False                          # trust AI/manual/session prose
    # Strict path: doc/heuristic prose must NAME something concrete. The anchor has to
    # appear in the text itself — a bare `files` entry is not enough, because the doc
    # scanner stamps the file it happens to be reading onto every line it extracts.
    # That made each line self-certifying and let whole paragraphs of PLAN.md through.
    named = any(_leaf(a) and _leaf(a) in t
                for a in list(files or []) + list(symbols or []))
    return not (named or _has_anchor(t))


def _leaf(path: str) -> str:
    """The part of an anchor a sentence would actually mention: `store.py` out of
    `omni_memory/store.py`, `create_order` out of `Service::create_order`."""
    return str(path or "").replace("\\", "/").split("/")[-1].split("::")[-1]


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
