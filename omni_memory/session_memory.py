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

EXTRACTION_PROMPT = """\
You are OmniMemory's extractor. From the conversation + git diff below, extract
durable project memory that a future coding session MUST know. Output ONLY a JSON
array; each item: {"kind": one of decision|fact|flow|gotcha|todo|api|db|endpoint,
"text": one concise sentence, "files": [paths], "symbols": [names]}.
Rules: capture DECISIONS (and why), non-obvious FACTS, request/data FLOWS,
GOTCHAs, and TODOs. Skip anything obvious from the code itself. No prose, JSON only.
"""


def remember(store: Store, root: Path, text: str, kind: str = "fact",
             files: Optional[list[str]] = None, symbols: Optional[list[str]] = None,
             source: str = "manual") -> Memory:
    branch = gitmeta.current_branch(root)
    commit = gitmeta._git(root, "rev-parse", "--short", "HEAD")
    m = Memory(text=text.strip(), kind=kind if kind in KINDS else "fact",
               branch=branch, files=files or [], symbols=symbols or [],
               commit_range=commit, source=source)
    return store.add_memory(m)


def remember_many(store: Store, root: Path, items: list[dict], source: str = "session") -> int:
    n = 0
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        remember(store, root, text, it.get("kind", "fact"),
                 it.get("files"), it.get("symbols"), source=source)
        n += 1
    return n


def capture_from_json(store: Store, root: Path, raw: str, source: str = "session") -> int:
    """Ingest the agent's extraction output (a JSON array of memory items)."""
    try:
        items = json.loads(raw)
        if isinstance(items, dict):
            items = items.get("memories") or items.get("items") or []
    except Exception:  # noqa: BLE001
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
