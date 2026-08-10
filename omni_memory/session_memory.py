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
from .store import KINDS, Memory, Store

_KINDS_LINE = ("decision|concept|flow|event|endpoint|db|component|"
               "gotcha|assumption|todo|fact")

# Prompt the SessionEnd hook hands to the model to extract memory from a session.
EXTRACTION_PROMPT = f"""\
You are OmniMemory's extractor. From the conversation + git diff below, extract
durable project memory a FUTURE coding session must know. Output ONLY a JSON
array; each item: {{"kind": one of {_KINDS_LINE}, "text": one concise sentence,
"files": [paths], "symbols": [names], "evidence": "verified"|"stated"|"inferred"}}.
Use "evidence": "verified" for things confirmed by outcome THIS session (a bug that
bit, a fix that worked, a test result), "inferred" for guesses, else "stated".
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
"symbols": [names], "evidence": "verified" | "stated" | "inferred"}}.
Set "evidence": "verified" only when confirmed by outcome (a test, a failure/fix,
explicit config), "inferred" when guessed from comments/naming/structure, else
"stated". This drives how much the memory is trusted and how fast it's pruned.
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
    if evidence not in ("verified", "stated", "inferred"):
        evidence = "stated"
    m = Memory(text=text.strip(), kind=kind if kind in KINDS else "fact",
               branch=branch, files=files or [], symbols=symbols or [],
               commit_range=commit, source=source, evidence=evidence)
    return store.add_memory(m)


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
        # agent capture / AI build can mark evidence; docs & AI default to inferred
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
                items = _heuristic_extract(raw)
        else:                                       # free: no `claude -p` on autopilot
            items = _heuristic_extract(raw)
    return remember_many(store, root, items, source=source)


# -- fallback heuristic extractor (no LLM) ----------------------------------
_PATTERNS = [
    (r"\b(decided|we'll use|chose|going with|switch(?:ed)? to)\b", "decision"),
    (r"\b(TODO|FIXME|need to|should)\b", "todo"),
    (r"\b(careful|gotcha|note that|watch out|don't|beware)\b", "gotcha"),
    (r"\b(endpoint|route|/api/|GET |POST |PUT |DELETE )\b", "endpoint"),
    (r"\b(table|column|schema|migration|kafka|queue|publish(?:es)?)\b", "flow"),
]


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
        items = _heuristic_extract(text)
        for it in items:
            it.setdefault("files", []).append(rel)
        a, _dropped = remember_many(store, root, items, source="doc")
        added += a
    return added, len(docs)


def _heuristic_extract(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip("-* \t")
        if len(line) < 12 or len(line) > 240:
            continue
        for pat, kind in _PATTERNS:
            if re.search(pat, line, re.I):
                out.append({"kind": kind, "text": line,
                            "files": re.findall(r"[\w/]+\.\w+", line)})
                break
    return out[:40]
