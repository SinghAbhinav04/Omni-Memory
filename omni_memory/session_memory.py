"""Capture — turn a session (transcript + diff) into structured memories.

The heavy lifting (semantic extraction) is done by the *agent's own model* via
the SessionEnd hook: it's handed EXTRACTION_PROMPT + the transcript, returns a
JSON list of memories, and calls `remember_many`. A lightweight heuristic
extractor is provided so the tool also works standalone / in tests.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from . import gitmeta
from .store import KINDS, Memory, Store, clamp_evidence

_KINDS_LINE = ("decision|concept|flow|event|endpoint|db|component|"
               "gotcha|assumption|todo|fact")

# Prompt the SessionEnd hook hands to the model to extract memory from a session.
EXTRACTION_PROMPT = f"""\
You are OmniMemory's extractor. From the conversation + git diff below, extract
durable project memory a FUTURE coding session must know. Output ONLY a JSON
array; each item: {{"kind": one of {_KINDS_LINE}, "text": one concise sentence,
"files": [paths], "symbols": [names], "evidence": "stated"|"inferred"}}.
Use "inferred" for guesses (pruned first) and "stated" for things you assert
directly; OmniMemory promotes a memory to "verified" itself once its code anchor
proves re-fetchable and the memory gets cited — you never set "verified".
Capture: DECISIONS (and why), request/data FLOWS, event/Kafka CONTRACTs, ENDPOINT
maps (controller->service->repo + key params), DB schema facts, reusable
COMPONENTs, GOTCHAs, and TODOs.
Skip anything trivially obvious from the code. No prose — JSON only.
"""

# Prompt for the one-time `omni-memory build`: seed memory from the whole repo.
BUILD_PROMPT = f"""\
You are OmniMemory doing a ONE-TIME bootstrap of this repository. Study the code,
config, and any docs, then output ONLY a JSON array of durable project memories:
{{"kind": one of {_KINDS_LINE}, "text": one concise sentence, "files": [paths],
"symbols": [names], "evidence": "stated" | "inferred"}}.
Use "inferred" when guessed from comments/naming/structure and "stated" when you
assert it directly. Do NOT emit "verified" — OmniMemory earns that tier itself
once the memory's code anchor stays re-fetchable and it gets cited. This drives
how much the memory is trusted and how fast it's pruned.
Prioritize, in order: (1) system ARCHITECTURE & key DECISIONs, (2) domain CONCEPTs,
(3) end-to-end FLOWs, (4) event/Kafka CONTRACTs, (5) ENDPOINT map
(path -> controller -> service -> repo/downstream + DTOs/params), (6) DB schema,
(7) reusable COMPONENTs, (8) GOTCHAs/known issues. Be specific (real names, paths,
topics). No prose — JSON only.
"""


_CITE = re.compile(r"\[([0-9a-f]{8,16})\]")


def extract_citations(text: str, valid_ids: set) -> list[str]:
    """Memory ids the agent cited in `text` (the enforce block asks for `[id]`s).
    Only ids that exist are returned — this is the relevance-feedback signal."""
    return [i for i in dict.fromkeys(_CITE.findall(text or "")) if i in valid_ids]


def remember(store: Store, root: Path, text: str, kind: str = "fact",
             files: Optional[list[str]] = None, symbols: Optional[list[str]] = None,
             source: str = "manual", evidence: str = "stated") -> Memory:
    branch = gitmeta.current_branch(root)
    commit = gitmeta._git(root, "rev-parse", "--short", "HEAD")
    # Reserved keyspace for trust: this is the path autonomous capture flows through
    # (session / ai-session / ai-build / doc), so a machine can't forge `verified`
    # here — clamp_evidence caps it by source. Human sources (manual) pass through, so
    # `remember --verified` still works. `verified` for machine memory is earned later
    # by staleness.graduate_verified, never self-declared.
    evidence = clamp_evidence(evidence, source)
    # Exact content identity for each anchored file, so staleness is re-fetchable
    # (blob sha match/miss) rather than a heuristic diff.
    shas, observed, unbound = _observe(store, root, files or [])
    m = Memory(text=text.strip(), kind=kind if kind in KINDS else "fact",
               branch=branch, files=files or [], symbols=symbols or [],
               commit_range=commit, source=source, evidence=evidence,
               blob_shas=shas, observed=observed, unbound=unbound,
               author=gitmeta.git_user(root))
    return store.add_memory(m)


def _observe(store: Store, root: Path, files: list[str]) -> tuple[dict, bool, bool]:
    """Bind a memory's provenance to the bytes the agent actually OBSERVED. For each
    file, prefer the read-ledger digest (recorded when the agent read/edited it) over
    the capture-time hash; if they disagree the file moved between read and capture
    (UNBOUND_CAPTURE). Returns ({path: observation digest}, observed, unbound):
      observed — every anchored file was read-ledger-backed (not just capture-time)
      unbound  — some file changed between when it was read and when we captured."""
    shas, observed, unbound = {}, bool(files), False
    for p in files:
        cap = gitmeta.blob_sha(root, p)               # bytes at capture (session end)
        read = store.read_ledger_get(p)               # bytes when the agent read it
        if read:
            shas[p] = read                            # bind to what was observed
            if cap and read != cap:
                unbound = True                        # moved between read and capture
        elif cap:
            shas[p] = cap                             # no read record → declared, not observed
            observed = False
        else:
            observed = False                          # file absent now — can't anchor it
    if not files:
        observed = False
    return shas, observed, unbound


def remember_many(store: Store, root: Path, items: list[dict],
                  source: str = "session") -> tuple[int, int]:
    """Store the given memory items; returns (added, dropped_as_noise) so callers
    can tell the user when the noise filter silently rejected candidates."""
    from . import cleanup
    items, dropped = cleanup.filter_items(items, source=source)
    n = 0
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        # Content may report `inferred` (a guess) to LOWER its own trust; it can
        # never RAISE it — `add_memory` caps the tier by source authority, so a
        # capture claiming `verified` is stored as `stated` and must earn `verified`.
        default_ev = "inferred" if source in ("ai-build", "doc") else "stated"
        remember(store, root, text, it.get("kind", "fact"),
                 it.get("files"), it.get("symbols"), source=source,
                 evidence=it.get("evidence", default_ev))
        n += 1
    return n, dropped


def capture_from_json(store: Store, root: Path, raw: str,
                      source: str = "session") -> tuple[int, int]:
    """Ingest memory from the agent's JSON, or extract it from a raw transcript.

    Order: (1) already-JSON → ingest; (2) a model is configured → semantic
    extraction via EXTRACTION_PROMPT (autonomous, headless); (3) heuristic.
    """
    from . import llm
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = items.get("memories") or items.get("items") or []
        if not isinstance(items, list):
            raise ValueError
    except Exception:  # noqa: BLE001
        if llm.autocapture_ok():                    # real API key / opt-in only
            try:
                items = llm.extract_memories(EXTRACTION_PROMPT, raw[:30_000])
                source = "ai-session"
            except Exception:  # noqa: BLE001
                items = _heuristic_extract(raw, root)
                source = "heuristic"                # noisy scanner → strict filter
        else:                                       # free: no `claude -p` on autopilot
            items = _heuristic_extract(raw, root)
            source = "heuristic"                    # require a concrete anchor
    return remember_many(store, root, items, source=source)


# -- fallback heuristic extractor (no LLM) ----------------------------------
# Ordered most-specific-first, and every pattern demands STRUCTURE rather than a
# mention. The old `\b(endpoint|route|...)\b` fired on any prose containing the word
# "endpoint", so half a transcript was filed as endpoint memory — and because
# `systemmap._ROLE_BY_KIND` resolves `endpoint` to `gateway` before anything else, the
# system map rendered almost every module as a gateway. A word is not a fact about the
# system; a method+path, an arrow chain, or a DDL verb is.
_PATTERNS = [
    (r"(?:\b(?:GET|POST|PUT|PATCH|DELETE)\s+/\S+|/api/[\w{}/-]+)", "endpoint"),
    (r"\S\s*(?:->|→|=>)\s*\S", "flow"),
    (r"\b(?:CREATE TABLE|ALTER TABLE|migration|foreign key|primary key|"
     r"\w+\.\w+ column|column \w+)\b", "db"),
    (r"\b(?:publishes|subscribes|consumes|emits)\b.{0,40}\b(?:topic|queue|event)\b"
     r"|\b(?:kafka|rabbitmq|sqs)\b", "event"),
    (r"\b(?:we decided|decided to|we'll use|chose|going with|switch(?:ed)? to)\b",
     "decision"),
    (r"\b(?:gotcha|watch out|beware|careful:|note that)\b", "gotcha"),
    (r"\b(?:TODO|FIXME)\b", "todo"),
    (r"\bis (?:a|an|the)\b.{6,}", "concept"),
]

# A path candidate: at least one real filename-ish segment ending in a plausible source
# extension. Deliberately NOT `[\w/]+\.\w+`, which matched "0.9", "3.11", "e.g" and
# "//pypi.org" — version numbers and hostnames wearing a dot. Every candidate is then
# checked against the working tree, because the only thing that makes a path an anchor
# is that it resolves.
_FILE_RE = re.compile(r"\b[\w.-]+(?:/[\w.-]+)*\.[A-Za-z][A-Za-z0-9]{0,4}\b")

# Transcripts are flattened to "role: text" by `cli._read_transcript`. The prefix is
# framing, not content, and keeping it turned chat turns into project facts.
_ROLE_PREFIX = re.compile(r"^\s*(?:assistant|user|system|human)\s*:\s*", re.I)


def _real_files(root: Path, line: str) -> list[str]:
    """Paths named in `line` that actually resolve to a file INSIDE the repo.

    An anchor that does not resolve is not an anchor. Enforcing that here is what stops
    a fake path from earning a blob sha it can never honour, and from reaching the
    system map as a building named after a version number.

    Anything outside the repo is dropped too: `..` segments survive the regex, and an
    anchor pointing out of the tree is one git cannot hash and the map cannot cite.
    """
    out = []
    root = Path(root).resolve()
    for cand in _FILE_RE.findall(line):
        # Strip a leading `./` only. `lstrip("./")` strips those CHARACTERS, which
        # quietly turns `../outside.py` into `outside.py` — re-pointing the anchor at a
        # different file instead of rejecting it.
        rel = cand[2:] if cand.startswith("./") else cand
        if not rel or rel in out:
            continue
        try:
            p = (root / rel).resolve()
            if p.is_file() and p.relative_to(root):
                out.append(rel)
        except (ValueError, OSError):
            continue                    # outside the repo, or unresolvable
    return out


_SKIP_DIRS = {"node_modules", ".git", ".omni-memory", "dist", "build",
              "__pycache__", ".venv", "venv", "target"}


def ingest_docs(store: Store, root: Path, max_files: int = 300) -> tuple[int, int]:
    """Seed memory from existing markdown docs (their kb) via the heuristic pass.
    Returns (memories_added, docs_scanned)."""
    docs = [p for p in root.rglob("*.md")
            if not (_SKIP_DIRS & set(p.parts))]
    added = 0
    for f in docs[:max_files]:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        rel = str(f.relative_to(root))
        items = _heuristic_extract(text, root)
        # Filter BEFORE stamping the doc's own path on. The strict noise path waives
        # its concrete-anchor requirement for anything that already names a file, so
        # stamping first made every scanned line self-certifying: `PLAN.md` is where we
        # happened to be reading, not evidence that the line is a durable fact.
        from . import cleanup
        items, _ = cleanup.filter_items(items, source="doc")
        for it in items:
            if rel not in it.setdefault("files", []):
                it["files"].append(rel)
        a, _dropped = remember_many(store, root, items, source="doc")
        added += a
    return added, len(docs)


def _heuristic_extract(text: str, root: Optional[Path] = None) -> list[dict]:
    """Scan raw text for candidate memories. Noisy by design — `cleanup.filter_items`
    is the gate — but it must not manufacture evidence: the role prefix is stripped so
    a chat turn isn't stored as a fact, and file anchors are resolved against `root`
    so a version number can't masquerade as a source."""
    out = []
    for line in text.splitlines():
        line = _ROLE_PREFIX.sub("", line.strip("-* \t")).strip()
        if len(line) < 12 or len(line) > 240:
            continue
        for pat, kind in _PATTERNS:
            if re.search(pat, line, re.I):
                out.append({"kind": kind, "text": line,
                            "files": _real_files(root, line) if root else []})
                break
    return out[:40]
