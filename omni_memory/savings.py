"""Token savings — what memory actually bought you, measured rather than claimed.

Every pull records two numbers: what the injected block cost (`served`), and what the
agent would have had to read to learn the same things without it (`baseline`). The
difference is the saving. Both are estimates and are labelled as such wherever they are
printed; the point is not a precise figure but a *falsifiable* one.

The baseline is the cheapest honest counterfactual, and it is deliberately conservative:

  * only files a served memory **cites and that still resolve today** are counted. A
    memory with no resolving anchor contributes nothing. We cannot claim to have saved a
    read that could never have happened, and pretending otherwise would make the number
    grow fastest exactly when capture quality is worst.
  * each distinct file counts **once per pull**, capped at `_FILE_CAP` tokens. An agent
    skims a large file; it does not read a megabyte to find one fact.
  * a flat `SEARCH_OVERHEAD` per pull stands in for the grep/glob round-trip that would
    have preceded the reads.

So the number under-reports. That is the right direction to be wrong in for a metric
whose whole purpose is to be trusted.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

CHARS_PER_TOKEN = 4
_FILE_CAP = 8_000          # tokens: nobody reads more of one file to answer one question
SEARCH_OVERHEAD = 250      # tokens: the grep/glob call + its result listing, per pull


def tokens(text: str) -> int:
    """Rough token count. ~4 chars/token — the same approximation the CLI has always
    used for its footprint report, kept in one place so both agree."""
    return (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def baseline_tokens(root: Path, memories) -> tuple[int, int]:
    """(baseline_tokens, files_counted) — the cost of re-reading the sources behind
    these memories instead of being handed them.

    Returns the search overhead alone when nothing resolves: the agent would still have
    paid for the search that found nothing.
    """
    total, counted = 0, 0
    seen = set()
    for m in memories or []:
        for rel in (m.get("files") or []):
            if rel in seen:
                continue
            seen.add(rel)
            p = root / rel
            try:
                if not p.is_file():
                    continue                      # a source that is gone earns no credit
                size = p.stat().st_size
            except OSError:
                continue
            total += min(size // CHARS_PER_TOKEN, _FILE_CAP)
            counted += 1
    return total + SEARCH_OVERHEAD, counted


def record(store, root: Path, block: str, memories, event: str,
           branch: str = "") -> dict:
    """Log one pull. Returns the row so a caller can report it immediately.

    Never raises: this is bookkeeping riding the injection path, and a ledger failure
    must not cost the user their memory block.
    """
    try:
        served = tokens(block)
        base, nfiles = baseline_tokens(root, memories)
        ids = [m.get("id") for m in (memories or []) if m.get("id")]
        store.add_saving(event=event, branch=branch, mem_ids=ids,
                         served=served, baseline=base, files=nfiles)
        return {"served": served, "baseline": base, "files": nfiles,
                "saved": max(0, base - served)}
    except Exception:  # noqa: BLE001
        return {"served": 0, "baseline": 0, "files": 0, "saved": 0}


def summary(store, days: int = 7) -> dict:
    """Cumulative totals plus a recent window, for `gain` and the dashboard."""
    row = store.db.execute(
        "SELECT COUNT(*) pulls, COALESCE(SUM(served),0) served, "
        "COALESCE(SUM(baseline),0) baseline, MIN(ts) first_ts, MAX(ts) last_ts "
        "FROM savings").fetchone()
    since = time.time() - days * 86400
    recent = store.db.execute(
        "SELECT COUNT(*) pulls, COALESCE(SUM(served),0) served, "
        "COALESCE(SUM(baseline),0) baseline FROM savings WHERE ts >= ?",
        (since,)).fetchone()
    sessions = store.db.execute(
        "SELECT COUNT(*) n FROM (SELECT 1 FROM savings WHERE event='session')"
    ).fetchone()["n"]

    def _pack(r):
        served, base = int(r["served"]), int(r["baseline"])
        saved = max(0, base - served)
        return {"pulls": int(r["pulls"]), "served": served, "baseline": base,
                "saved": saved, "pct": round(100.0 * saved / base, 1) if base else 0.0}

    out = _pack(row)
    out["sessions"] = int(sessions)
    out["first_ts"] = row["first_ts"]
    out["last_ts"] = row["last_ts"]
    out["recent"] = _pack(recent)
    out["recent_days"] = days
    out["method"] = (
        "baseline = sum of the source files each served memory cites "
        f"(capped {_FILE_CAP // 1000}k tok/file) + {SEARCH_OVERHEAD} tok search overhead "
        "per pull. Memories with no resolving anchor earn no credit. "
        f"Estimate, ~{CHARS_PER_TOKEN} chars/token.")
    return out


def history(store, limit: int = 20) -> list[dict]:
    rows = store.db.execute(
        "SELECT ts, event, branch, mem_ids, served, baseline, files "
        "FROM savings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["mem_ids"] = json.loads(d.get("mem_ids") or "[]")
        d["saved"] = max(0, int(d["baseline"]) - int(d["served"]))
        out.append(d)
    return out


def human(n: int) -> str:
    """Compact token counts: 1234 -> '1.2k', 1_234_567 -> '1.2M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)
