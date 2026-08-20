"""Collector liveness — checked against a witness the collector does not own.

The observation ledger is written by a `PostToolUse` hook. A hook can die *silently*:
a bad interpreter, an import error, a wrong plugin path — all of which print something
and exit 0. Nothing looks broken. The agent keeps working. The ledger simply stops being
written, `observation_binding_coverage` decays toward zero, and every record already in
the store keeps reading `observed`.

The failure is invisible from inside the data, because two states are indistinguishable
there: a session where the agent read nothing, and a session where the collector was
dead the whole time. Both produce "no new observations". One is a quiet week; the other
is a broken guarantee — and it fails in the direction of confidence.

So the check cannot live inside the thing it checks. The witness used here is the session
transcript that the Claude Code runtime writes itself: a different code path, with no
knowledge that our ledger exists. "The agent touched N files and the ledger recorded
none" is then an alertable contradiction rather than an unfalsifiable silence.

KNOWN LIMIT, stated rather than glossed: the transcript is written by the same runtime
that runs our hooks. If the runtime dies, both counters vanish together and this reports
SKIP, not FAIL. It is external to the collector, not external to the runtime — a check
below the runtime (kernel-side) would be strictly stronger, at a cost this does not pay.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

# The tools whose use means the agent actually looked at a file's bytes. A Bash command
# that happened to `cat` a file is deliberately NOT here: the agent did not read it
# through an instrumented tool, so we cannot claim it observed the contents.
OBSERVED_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def transcript_dir(root: Path) -> Path:
    """Where the runtime keeps this project's session transcripts."""
    slug = str(Path(root).resolve()).replace(os.sep, "-")
    return Path.home() / ".claude" / "projects" / slug


def latest_transcript(root: Path) -> Optional[Path]:
    """The most recently written session transcript for this project, if any."""
    d = transcript_dir(root)
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def count_tool_reads(transcript: Optional[Path], root: Path) -> Optional[dict]:
    """How many in-repo files the agent touched through an instrumented tool, counted
    from the RUNTIME's transcript — not from anything the collector wrote.

    Returns None (not 0) when there is no transcript: an absent witness must not be
    reported as evidence of anything. Distinct paths are returned alongside the raw
    count because the ledger is keyed by path, so only the distinct figure is
    comparable to it.
    """
    if not transcript or not Path(transcript).is_file():
        return None
    reads, paths, session = 0, set(), None
    root = Path(root).resolve()
    try:
        text = Path(transcript).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        session = session or e.get("sessionId")
        content = (e.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not (isinstance(b, dict) and b.get("type") == "tool_use"):
                continue
            if b.get("name") not in OBSERVED_TOOLS:
                continue
            fp = (b.get("input") or {}).get("file_path") or ""
            if not fp:
                continue
            try:                                   # only files the ledger would record
                rel = Path(fp).resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            reads += 1
            paths.add(str(rel))
    return {"reads": reads, "paths": len(paths), "session": session}


def liveness(store, root: Path, transcript: Optional[Path] = None) -> dict:
    """Three states, not two.

        FAIL — the agent touched files and the ledger is empty: the hook is
               unregistered, crashing, or silently failing.
        SKIP — no transcript to check against, or the agent touched nothing. Never OK:
               proving a collector alive from its own output is the forbidden case, and
               "no discrepancy" is not "collector alive".
        OK   — the agent touched files and observations were recorded.

    Note the comparison is deliberately NOT equality. `read_ledger` is keyed by path, so
    re-reading one file adds no row; the ledger legitimately holds fewer entries than the
    transcript has reads. Only "some reads, zero observations" is a contradiction.
    """
    ext = count_tool_reads(transcript or latest_transcript(root), root)
    observations = store.read_ledger_count()
    if ext is None:
        return {"verdict": "SKIP", "reads": None, "observations": observations,
                "why": "no session transcript to check against — the collector cannot be "
                       "verified from its own output",
                "remedy": "run from a Claude Code session so a transcript exists"}
    if ext["reads"] == 0:
        return {"verdict": "SKIP", "reads": 0, "observations": observations,
                "why": "the agent touched no in-repo files this session — nothing to "
                       "check (NOT the same as 'the collector is alive')",
                "remedy": ""}
    if observations == 0:
        return {"verdict": "FAIL", "reads": ext["reads"], "observations": 0,
                "why": f"agent touched {ext['paths']} in-repo file(s) "
                       f"({ext['reads']} tool call(s)) and the ledger is EMPTY — the read "
                       "hook is unregistered, crashing, or exiting 0 without writing",
                "remedy": "omni-memory bind — then check `python3` resolves to a real "
                          "interpreter (a Store stub prints 'Python' and exits 49)"}
    return {"verdict": "OK", "reads": ext["reads"], "observations": observations,
            "why": f"{ext['paths']} in-repo file(s) touched, {observations} observation(s) "
                   "recorded",
            "remedy": ""}
