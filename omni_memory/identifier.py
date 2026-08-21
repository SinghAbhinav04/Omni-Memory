"""Identifier contract — declared, then re-measured against the store's own keys.

Memory ids are `uuid4().hex[:12]`: a truncation fold. Declaring a fold is old and
well-solved; what goes wrong in practice is that the declaration is never re-checked
against the live data, so a fold that was free when it was chosen quietly becomes lossy
as the population grows. A doc goes stale. A query re-measures.

Three independent things are reported, because they answer different questions:

  form     — do the ids in the store actually LOOK like the declared shape? This is not
             hypothetical: `Store.import_memories` preserves a teammate's id verbatim
             from a committed shard, so version skew or a hand-edited shard injects
             foreign ids that no amount of local correctness prevents.

  cost     — how much would the fold lose on THIS store's keys? Reported three-valued,
             never as a bare zero. On a small population a zero is the *absence of a
             signal* — collisions are not expected yet regardless of whether the fold is
             any good — and it renders exactly like a clean bill of health.

  cliff    — the longest truncation that ALREADY merges the keys in front of you. No
             statistical model is involved: it is a property of the keys, not an estimate
             about a population. It is therefore reported UNCONDITIONALLY, never gated
             behind the cost verdict.

The gating is the part that was wrong, and it was wrong in a way that could not be seen
from a passing test. On structured keys (paths, `file::symbol`) the threshold at the
first non-merging fold runs to 10^27 and beyond, so a model-gated cliff warning is not
merely quiet — it is *unsatisfiable*, and stays unsatisfiable however the store grows.
Measured on this repo's own code graph: 602 node ids, cliff at 56 characters, and
`ZERO_AT_SCALE` returned at no fold length from 1 to 106. A warning that cannot fire and
a warning that found nothing render identically, so when the cliff block is silent it now
says WHY (`reason_empty`).

Two further limits travel with the cliff, because one number cannot carry them:

  · On variable-length keys the cliff describes the population's right edge and nothing
    to its left. On the same 602 ids, 427 are SHORTER than the cliff — for them the fold
    is the identity and no distance to the cliff exists. So `key_length_min/max`,
    `fixed_length` and `keys_shorter_than_cliff` are reported beside it, and a reader can
    see whether the single number applies to that store at all.

  · A measurement can be correct and still be read for a question it cannot answer. So a
    report carries a `population_commitment` that names its own SCOPE — which fields the
    measured quantity depends on — and verification answers two questions separately:
    does the commitment verify, and is it sufficient for the predicate being asked.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Callable, Iterable, Optional, Sequence

# The declaration: what `Store.add_memory` mints (uuid4().hex[:12]).
ID_LENGTH = 12
DECLARED_FORM = re.compile(r"^[0-9a-f]{12}$")
DECLARED = "uuid4().hex[:12] — 12 lowercase hex characters"

#: bumped when a change here would alter a previously-minted commitment's digest or
#: meaning, so an old pin can be told apart from a stale one.
CONTRACT_VERSION = 2

# Which fields a measured quantity depends on. A commitment over `keys` is sufficient to
# carry a headroom measurement forward and is NOT sufficient to carry "is this observation
# still current" — same digest function, same store, opposite answers.
SCOPE_FOR = {
    "headroom": ("key",),
    "fold_cost": ("key",),
    "observation_current": ("key", "digest"),
}


def fold_cost(keys: Iterable[str], fold: Callable[[str], str]) -> dict:
    """What `fold` would cost on these keys.

    Returns two numbers a reader could easily confuse, so they are named apart and the
    tests are required to keep them divergent:
        groups_colliding — how many buckets ended up holding more than one key
        keys_lost        — how many keys would actually disappear
    They are equal only when every collision is a plain pair; a single 3-way merge makes
    them differ, which is exactly the fixture a swap-mutant survives without.
    """
    groups: dict[str, set] = {}
    n = 0
    for k in keys:
        n += 1
        groups.setdefault(fold(k), set()).add(k)
    colliding = [g for g in groups.values() if len(g) > 1]
    return {"keys": n, "buckets": len(groups),
            "groups_colliding": len(colliding),
            "keys_lost": sum(len(g) - 1 for g in colliding)}


def measured_space(keys: list[str], length: int) -> dict:
    """The size of the keyspace the fold actually ranges over, measured from the keys
    rather than assumed from an alphabet.

    A hex bound is wrong on structured identifiers: keys sharing a directory prefix have
    near-zero entropy in their leading positions, and the analytic threshold would say
    "safe" for a fold that merges everything. So each position contributes its own
    perplexity (exp of its entropy), and the space is their product.

    Three biases travel with it, and they make the verdicts unequal:
      · positions are treated as independent, which shared prefixes INFLATE;
      · a plug-in entropy from n samples cannot see an alphabet wider than n, which
        DEFLATES it — reported as `positions_saturated`;
      · on variable-length keys, a position past most keys' end is estimated from the
        few keys long enough to have it, and that thin subsample still multiplies into
        the product — reported as `positions_undersampled`. This is what drives the
        threshold at a long fold to absurd magnitudes, so it is named rather than left
        for a reader to infer from the exponent.
    So `keys < threshold` is sound; `keys >= threshold` is the weaker claim.
    """
    if not keys:
        return {"space": 1.0, "positions_saturated": 0, "positions_undersampled": 0}
    n = len(keys)
    space, saturated, undersampled = 1.0, 0, 0
    for i in range(length):
        chars = [k[i] for k in keys if len(k) > i]
        if not chars:
            continue
        counts = Counter(chars)
        total = len(chars)
        h = -sum((c / total) * math.log(c / total) for c in counts.values())
        space *= math.exp(h)
        if len(counts) == total:        # as many distinct chars as samples: the estimate
            saturated += 1              # is the sample size in disguise, not the alphabet
        if total < n:                   # only some keys reach this far
            undersampled += 1
    return {"space": space, "positions_saturated": saturated,
            "positions_undersampled": undersampled}


def population_threshold(space: float, p: float = 0.01) -> int:
    """Birthday bound: how many keys before a collision is >= p likely in `space`."""
    if space <= 1:
        return 1
    return max(1, math.ceil(math.sqrt(2 * space * math.log(1 / (1 - p)))))


def fold_verdict(cost: dict, space: dict, threshold: int) -> dict:
    """Three-valued, because a zero has two entirely different causes and only one of
    them is good news. A bare boolean here would be true and due to stop being true
    without ever saying so."""
    if cost["keys_lost"] > 0:
        # A measurement always outranks the model — if keys demonstrably merge, no
        # threshold argument makes that fine.
        return {"verdict": "COST_MEASURED", "threshold": threshold,
                "why": f"{cost['keys_lost']} key(s) would be lost across "
                       f"{cost['groups_colliding']} colliding group(s)"}
    if cost["keys"] < threshold:
        return {"verdict": "NOT_YET_MEASURABLE", "threshold": threshold,
                "why": f"{cost['keys']} keys against a ~{threshold} threshold: zero loss "
                       "is expected by the birthday bound and is NOT a property of the fold"}
    if space["positions_saturated"]:
        return {"verdict": "NOT_YET_MEASURABLE", "threshold": threshold,
                "why": f"{space['positions_saturated']} position(s) saturated — the space "
                       "estimate is the sample size in disguise, so 'at scale' is unearned"}
    return {"verdict": "ZERO_AT_SCALE", "threshold": threshold,
            "why": "zero loss on a population past the threshold — a property of the fold"}


# ── the cliff: a property of the keys, measured, never gated on a model ──────

def _merges_at(keys: Sequence[str], length: int) -> bool:
    """Does truncating to `length` characters merge any two of these keys?"""
    return fold_cost(keys, lambda k, L=length: k[:L])["keys_lost"] > 0


def collides_at_length_linear(keys: Sequence[str], max_length: int) -> Optional[int]:
    """Reference implementation: scan down from `max_length`. Kept in the shipped module
    rather than in the tests so the fast path is checked against a definition that lives
    at the same version — an optimisation that changes the answer is not one."""
    for L in range(max_length, 0, -1):
        if _merges_at(keys, L):
            return L
    return None


def collides_at_length(keys: Sequence[str], max_length: Optional[int] = None) -> Optional[int]:
    """The longest truncation that still merges two of these keys, or None if no fold
    does. Binary search, because the predicate is monotone: if a prefix of length L
    merges two keys then every shorter prefix merges them too, so the answer is the last
    True and a bisection finds it in log(max_length) evaluations rather than max_length.

    `max_length` defaults to the longest key present, NOT to the declared fold. Capping
    the search at the declared fold is the defect this replaced: it cannot find a cliff
    that sits above the fold, and on this repo's code-graph ids (8..106 chars, cliff at
    56) it reported a cliff at 11 — an artefact of the search window, not a fact about
    the keys, and one that reads as *more* alarming than the truth.
    """
    keys = list(keys)
    if len(keys) < 2:
        return None
    hi = max_length if max_length is not None else max(len(k) for k in keys)
    if hi < 1:
        return None
    if not _merges_at(keys, 1):
        return None                      # not even a 1-character fold merges them
    if _merges_at(keys, hi):
        return hi                        # duplicates, or the cap is below the true cliff
    lo = 1                               # invariant: merges(lo) and not merges(hi)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _merges_at(keys, mid):
            lo = mid
        else:
            hi = mid
    return lo


def key_shape(keys: Sequence[str]) -> dict:
    """Whether a single cliff number can characterise this population at all.

    On fixed-length keys (hashes, uuids, ULIDs) the cliff and the loss curve carry the
    same information, which is why the distinction is easy to miss — every metric here
    was built on such a population. On variable-length keys the cliff is a point at the
    right edge and says nothing about the curve to its left.
    """
    keys = [k for k in keys if k]
    if not keys:
        return {"keys": 0, "unique": 0, "key_length_min": 0, "key_length_max": 0,
                "fixed_length": True}
    lens = [len(k) for k in keys]
    return {"keys": len(keys), "unique": len(set(keys)),
            "key_length_min": min(lens), "key_length_max": max(lens),
            "fixed_length": min(lens) == max(lens)}


def headroom(keys: list[str], length: int = ID_LENGTH) -> dict:
    """How much slack the declared fold has, with no statistical model involved: the
    longest truncation that would ALREADY merge the keys in front of you.

    `headroom_chars` is how many characters you could drop before that happens. It
    answers a different question from the population threshold — "how much slack" versus
    "how many more keys" — and on structured keys the two disagree: a huge, well-past-
    threshold population of paths sharing a 20-character prefix reads perfectly safe by
    population and sits one character from collapsing by headroom.

    It is only a meaningful number when the caller actually folds at `length` AND every
    key is at least that long. When the keys are longer than the fold the cliff can sit
    ABOVE it (headroom is then zero or negative: the declared fold is already past the
    cliff), and when they are shorter the fold is the identity and there is no distance
    to measure. Both cases are reported in `headroom_applies` / `why_not` instead of
    being flattened into a number that looks like slack.
    """
    keys = [k for k in keys if k]
    shape = key_shape(keys)
    worst = collides_at_length(keys, max_length=max(shape["key_length_max"], length))
    shorter = sum(1 for k in keys if len(k) < worst) if worst else 0
    applies, why_not = True, None
    if worst is None:
        applies, why_not = False, "no fold merges these keys"
    elif not shape["fixed_length"]:
        applies, why_not = False, (
            f"keys vary in length ({shape['key_length_min']}..{shape['key_length_max']}); "
            f"{shorter} of {shape['keys']} are shorter than the cliff, so for them the "
            "fold is the identity and no distance to the cliff exists")
    return {"collides_at_length": worst,
            "first_clean_fold": (worst + 1) if worst is not None else 1,
            "headroom_chars": (length - worst) if worst is not None else length,
            "keys_shorter_than_cliff": shorter,
            "declared_length": length,
            "headroom_applies": applies,
            "headroom_why_not": why_not,
            **{k: shape[k] for k in ("key_length_min", "key_length_max", "fixed_length")}}


def cliff_block(keys: list[str], fold: Optional[int] = ID_LENGTH) -> dict:
    """The cliff, plus the reason the at-cliff-edge warning is silent when it is.

    An empty warning has at least three causes and they mean opposite things:
      no_fold_merges_these_keys        nothing collides at any length — genuinely clear
      threshold_unreachable_at_fold    the model cannot license "safe" at that fold, so a
                                       verdict-gated warning is DEAD CODE on this shape
                                       of key, not quiet
      positions_saturated              the space estimate is the sample size in disguise
      None                             the warning is genuinely firing

    `fold` is the length the CALLER folds at; pass None for a store whose keys are used
    whole (our `code_nodes.id` is a full primary key — nothing truncates it), in which
    case the distance to the cliff is undefined rather than zero.
    """
    keys = [k for k in keys if k]
    h = headroom(keys, length=fold if fold else max(1, key_shape(keys)["key_length_max"]))
    worst = h["collides_at_length"]
    clean = h["first_clean_fold"]
    space = measured_space(keys, clean)
    thr = population_threshold(space["space"])

    if worst is None:
        reason = "no_fold_merges_these_keys"
    elif len(keys) < thr:
        reason = "threshold_unreachable_at_fold"
    elif space["positions_saturated"]:
        reason = "positions_saturated"
    else:
        reason = None

    distance = None
    if fold is not None and worst is not None and h["fixed_length"]:
        distance = fold - worst
    return {
        "collides_at_length": worst,
        "first_clean_fold": clean,
        "threshold_at_first_clean_fold": thr,
        "keys_shorter_than_cliff": h["keys_shorter_than_cliff"],
        "fold": fold,
        "distance_to_cliff": distance,
        "distance_why_not": (None if distance is not None else
                             "keys are not folded" if fold is None else
                             h["headroom_why_not"] or "no fold merges these keys"),
        "reason_empty": reason,
        **{k: h[k] for k in ("key_length_min", "key_length_max", "fixed_length")},
    }


def loss_curve(keys: list[str], points: int = 12) -> list[dict]:
    """The shape the cliff projects away: keys lost at a spread of fold lengths.

    Not a report line — a chart. It exists because on a variable-length population the
    cliff is one point on this curve and reporting it alone says nothing about the rest:
    on our code graph a 12-character fold destroys 588 of 602 keys while the cliff sits
    at 56 and destroys two.
    """
    keys = [k for k in keys if k]
    if len(keys) < 2:
        return []
    hi = max(len(k) for k in keys)
    ls = sorted({max(1, round(hi * i / points)) for i in range(1, points + 1)} | {1, hi})
    out = []
    for L in ls:
        c = fold_cost(keys, lambda k, L=L: k[:L])
        out.append({"length": L, "keys_lost": c["keys_lost"],
                    "groups_colliding": c["groups_colliding"],
                    "keys_shorter": sum(1 for k in keys if len(k) < L)})
    return out


# ── commitment: cryptographic validity is not evidentiary sufficiency ────────

def population_commitment(keys: Iterable[str], verifies: str = "headroom",
                          tenant: str = "") -> dict:
    """Bind a measurement to the exact key set it was taken over.

    Order-independent by construction (sorted before hashing), so two enumerations of the
    same population commit alike — that is a test, not an assumption.

    The count is carried BESIDE the digest and never instead of it: substituting one key
    leaves the count identical, so a count-based check reports valid for a population that
    was swapped wholesale.

    `scope` names the fields the measured quantity depends on, and `verifies` names the
    predicate it was minted for. Without them the name of the struct carries semantic
    weight by implication: a commitment over keys is exactly right for a headroom claim
    and silently wrong for "is this observation still current", which depends on
    (key, digest). It would verify clean while being used for something it cannot see.
    """
    ks = sorted(k for k in keys if k)
    h = hashlib.sha256()
    for k in ks:
        h.update(k.encode("utf-8", "surrogatepass") + b"\x00")
    return {"count": len(ks), "digest": h.hexdigest(),
            "scope": list(SCOPE_FOR.get(verifies, ("key",))),
            "verifies": verifies, "tenant": tenant,
            "alg": "sha256", "contract_version": CONTRACT_VERSION}


def verify_commitment(pinned: dict, keys: Iterable[str], tenant: str = "") -> dict:
    """Is the pinned measurement still about the population in front of us?

    Three outcomes rather than a boolean, because "changed" and "swapped" have different
    remedies and a same-size swap is the case a cheap implementation gets wrong:

        MEASUREMENT_VALID    digest matches
        POPULATION_CHANGED   count differs — the measurement is about a different-sized set
        POPULATION_SWAPPED   count identical, digest differs — same size, new membership

    A tenant mismatch is reported ahead of all three: two tenants must never be handed the
    same bytes and read them as agreement. `tenant` is the CALLER's, not the pin's —
    taking it from the pin is how the check quietly agrees with itself.
    """
    cur = population_commitment(keys, verifies=pinned.get("verifies", "headroom"),
                                tenant=tenant)
    if pinned.get("tenant", "") != cur["tenant"]:
        return {"status": "FOREIGN_SCOPE", "valid": False,
                "why": "commitment was minted for a different tenant"}
    if pinned.get("contract_version") != cur["contract_version"]:
        return {"status": "CONTRACT_VERSION_CHANGED", "valid": False,
                "why": f"minted by contract v{pinned.get('contract_version')}, "
                       f"now v{cur['contract_version']} — remeasure rather than compare"}
    if pinned.get("digest") == cur["digest"]:
        return {"status": "MEASUREMENT_VALID", "valid": True,
                "why": f"same {cur['count']} keys as when measured"}
    if pinned.get("count") != cur["count"]:
        return {"status": "POPULATION_CHANGED", "valid": False,
                "why": f"measured over {pinned.get('count')} keys, now {cur['count']}"}
    return {"status": "POPULATION_SWAPPED", "valid": False,
            "why": f"still {cur['count']} keys, but not the same ones — a count-based "
                   "check would have called this valid"}


def sufficient_for(commitment: dict, predicate: str) -> dict:
    """The second question, kept apart from the first.

    A commitment can verify and still be evidentially insufficient for what a consumer is
    asking of it. Verification asks "is this the same population"; sufficiency asks "does
    this cover the fields my predicate depends on". Answering only the first is how a
    correctly-named commitment gets read as more than it claims.
    """
    need = set(SCOPE_FOR.get(predicate, ()))
    have = set(commitment.get("scope") or ())
    if not need:
        return {"sufficient": False, "why": f"unknown predicate {predicate!r}",
                "missing": []}
    missing = sorted(need - have)
    return {"sufficient": not missing, "missing": missing,
            "why": (f"scope {sorted(have)} covers {predicate}" if not missing else
                    f"{predicate} depends on {sorted(need)}; this commitment covers "
                    f"{sorted(have)} — missing {missing}")}


# ── the two populations this store actually keys on ─────────────────────────

def populations(store) -> list[dict]:
    """Every key set in the store that a collision would actually corrupt, measured the
    same way. Two of them, and they behave oppositely:

      memory.id     hash-shaped, fixed length, folded at 12 — the cliff sits far below
                    the fold and headroom is the number that means something.
      code_nodes.id structured (`path` or `path::symbol`), variable length, NEVER folded
                    — the cliff is real and adjacent, the distance to it is undefined,
                    and no model verdict can license "safe" at any length.

    Reporting only the first is how a store can look clean while its larger, structurally
    riskier population is outside the instrument entirely.
    """
    out = []
    for name, sql, fold in (
            ("memory.id", "SELECT id FROM memory", ID_LENGTH),
            ("code_nodes.id", "SELECT id FROM code_nodes", None)):
        try:
            keys = [r["id"] for r in store.db.execute(sql) if r["id"]]
        except Exception:  # noqa: BLE001 — a table may not exist on an older store
            continue
        if not keys:
            continue
        out.append({"population": name, "folded_at": fold,
                    **key_shape(keys), **cliff_block(keys, fold)})
    return out


def identifier_contract(store, prefix_folds: Optional[Sequence[int]] = None) -> dict:
    """The declaration plus what it costs on this store's own keys, measured on call.

    `prefix_folds` lets a caller name the fold ITS system uses instead of inheriting ours.
    Two lengths we picked are not a claim about anyone else's keys.
    """
    keys = [r["id"] for r in store.db.execute("SELECT id FROM memory")]
    folds = list(prefix_folds or [ID_LENGTH])
    off_form = [k for k in keys if not DECLARED_FORM.match(k or "")]
    cost = fold_cost(keys, lambda k: k[:ID_LENGTH])
    space = measured_space(keys, ID_LENGTH)
    verdict = fold_verdict(cost, space, population_threshold(space["space"]))
    return {"declared": DECLARED, "keys": len(keys),
            "off_form": off_form[:20], "off_form_count": len(off_form),
            "cost": cost, "space": round(space["space"], 1),
            "positions_saturated": space["positions_saturated"],
            "positions_undersampled": space["positions_undersampled"],
            **verdict,
            **headroom(keys),
            "cliff": {str(f): cliff_block(keys, f) for f in folds},
            "populations": populations(store),
            "commitment": population_commitment(keys, verifies="headroom"),
            }
