"""The VERIFY → USE window — carrying a verification forward to the moment of action.

Everything else in this library verifies at *pull*: `inject` refreshes, re-resolves blob
shas, and hands the agent a block it can trust. Nothing then carried that check forward
to the moment the agent actually *acted* on a memory. A memory pulled fresh at the start
of a task can go stale ten minutes later, mid-task, and `⚠STALE` would not know — it is
refresh-time, not use-time.

The window was never invisible; a second `check` sees it. It was *unbound*: nothing tied
the verification to the use, so the check that would have caught it is the one nobody
thinks to re-run. That is worse than an invisible failure, because the answer looks
verified.

So: pin each pulled memory's source digests at retrieval, and re-verify those pins at the
point the agent cites the memory. Two answers are kept apart throughout, because they
have different remedies:

    stale_at_use     the world moved after we checked   -> revalidate
    orphaned_at_use  the source vanished after we checked -> re-source, not revalidate

and the witness's own integrity is a third, separate question: did it bind anything at
all? A witness that bound nothing must say the world was NOT checked. It must never say
"clean" — `if not mismatches: return ok` is the natural shape of the code and the wrong
answer, because it cannot tell "I checked and found nothing wrong" from "I checked
nothing".
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from . import gitmeta
from .store import Store

OBSERVED, PIN_TIME = "observed", "pin"


def pin(store: Store, root: Path, memories: Iterable[dict]) -> int:
    """Pin the sources each pulled memory is being trusted on, as they are RIGHT NOW.

    The digest recorded is the current one, not the one captured long ago: this window
    asks "did the world move since I checked", so the pin must be the state at the check.

    The KIND of binding travels with each pin. A pin backed by a read-ledger entry is
    observation-bound — we know the agent saw those bytes. A pin we hashed ourselves is
    pin-time-bound: it answers whether the source moved since the check, NOT whether the
    memory was right about the bytes it read. Collapsing the two would re-commit, one
    level up, the exact conflation this whole design exists to undo.
    """
    mems = list(memories)
    # One batched hash for every path in the pull. This runs on the injection hot path,
    # so a subprocess per file would make a large pull stall.
    current = gitmeta.blob_shas(root, sorted({p for m in mems for p in (m.get("files") or [])}))
    n = 0
    for m in mems:
        for path in (m.get("files") or []):
            cur = current.get(path)
            if not cur:
                continue                       # nothing to pin: reported as unbound later
            read = store.read_ledger_get(path)
            store.witness_pin(m["id"], path, cur,
                              OBSERVED if read == cur else PIN_TIME)
            n += 1
    store.db.commit()
    return n


def verify(store: Store, root: Path, mem_ids: Iterable[str]) -> dict:
    """Re-read the pinned sources at the point of use and report what moved.

    Returns `valid` (did we actually check the world?) separately from the findings, and
    coverage as a fraction rather than a boolean, so a half-covered answer can never read
    as a fully-covered one.
    """
    ids = [i for i in dict.fromkeys(mem_ids) if i]
    stale, orphaned, unbound = [], [], []
    bound = total = observation_bound = pin_time_bound = 0
    checked = 0

    pins_by_id = {mid: store.witness_for(mid) for mid in ids}
    # One batched hash for every pinned path. A subprocess per pin made verification at
    # the point of use cost proportional to how much the agent had pulled — exactly
    # backwards, since a big pull is when this check matters most.
    now_shas = gitmeta.blob_shas(
        root, sorted({p["path"] for ps in pins_by_id.values() for p in ps}))

    for mid in ids:
        pins = pins_by_id[mid]
        mem = store.get_memory(mid) or {}
        declared = list(mem.get("files") or [])
        if not pins and not declared:
            continue                           # nothing was ever pullable here
        checked += 1
        pinned_paths = {p["path"] for p in pins}
        for path in declared:                  # a source we could not pin at all
            if path not in pinned_paths:
                total += 1
                unbound.append({"mem_id": mid, "path": path,
                                "why": "no digest could be resolved at pull time"})
        for p in pins:
            total += 1
            bound += 1
            observation_bound += p["kind"] == OBSERVED
            pin_time_bound += p["kind"] == PIN_TIME
            now = now_shas.get(p["path"], "")
            if not now:
                # RULE: a source that vanished after the pin is NOT stale_at_use. Silence
                # is not agreement, and "re-source" is a different remedy from
                # "revalidate" — folding them together loses the instruction.
                orphaned.append({"mem_id": mid, "path": p["path"], "kind": p["kind"]})
            elif now != p["digest"]:
                stale.append({"mem_id": mid, "path": p["path"], "kind": p["kind"],
                              "pinned": p["digest"][:12], "current": now[:12]})

    # RULE: a witness that bound nothing reports that the world was NOT checked. This is
    # the assertion most often gotten wrong, because the natural shape of the code
    # ("no mismatches -> ok") returns clean for it.
    valid = bound > 0
    return {
        "checked": checked, "valid": valid,
        "bound": bound, "total": total,
        "sources_bound": f"{bound}/{total}" if total else "0/0",
        "observation_bound": observation_bound, "pin_time_bound": pin_time_bound,
        "stale_at_use": stale, "orphaned_at_use": orphaned, "unbound": unbound,
        "summary": (
            "the world was NOT checked — no source was bound at pull time"
            if not valid else
            f"{len(stale)} source(s) moved and {len(orphaned)} vanished since the check"
            if (stale or orphaned) else
            f"all {bound} bound source(s) unchanged since the check"),
        # The limit travels with the answer: a pin-time binding says the source has not
        # moved since we looked, never that the memory was right about what it read.
        "limits": ([] if not pin_time_bound else
                   [f"{pin_time_bound} pin(s) are pin-time-bound, not observation-bound: "
                    "they answer whether the source moved since the check, not whether "
                    "the memory was right about the bytes it read"]),
    }


def note_on_use(store: Store, root: Path, mem_ids: Iterable[str]) -> Optional[str]:
    """Verify at the point of use and, when the window opened, leave a durable note.

    Detecting this retrospectively (at capture, from the ids the agent cited) still
    matters: it says the memory the agent ACTED on had already moved, so the work may
    rest on a stale answer. That is worth a cross-session event even after the fact.
    """
    r = verify(store, root, mem_ids)
    if not r["valid"] or not (r["stale_at_use"] or r["orphaned_at_use"]):
        return None
    bits = []
    if r["stale_at_use"]:
        bits.append(", ".join(sorted({s["path"] for s in r["stale_at_use"]}))
                    + " moved after it was pulled (revalidate)")
    if r["orphaned_at_use"]:
        bits.append(", ".join(sorted({s["path"] for s in r["orphaned_at_use"]}))
                    + " vanished after it was pulled (re-source)")
    msg = "⚠ stale-at-use: " + "; ".join(bits)
    store.add_event(gitmeta.current_branch(root), msg)
    return msg
