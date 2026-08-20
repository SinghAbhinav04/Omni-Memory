"""Identifier contract — declared, then re-measured against the store's own keys.

Memory ids are `uuid4().hex[:12]`: a truncation fold. Declaring a fold is old and
well-solved; what goes wrong in practice is that the declaration is never re-checked
against the live data, so a fold that was free when it was chosen quietly becomes lossy
as the population grows. A doc goes stale. A query re-measures.

Two independent things are reported, because they answer different questions:

  form     — do the ids in the store actually LOOK like the declared shape? This is not
             hypothetical: `Store.import_memories` preserves a teammate's id verbatim
             from a committed shard, so version skew or a hand-edited shard injects
             foreign ids that no amount of local correctness prevents.

  cost     — how much would the fold lose on THIS store's keys? Reported three-valued,
             never as a bare zero. On a small population a zero is the *absence of a
             signal* — collisions are not expected yet regardless of whether the fold is
             any good — and it renders exactly like a clean bill of health.

A companion, `headroom`, needs no statistical model at all: how many characters shorter
the fold would have to be before it merged the keys already in front of you. The
threshold answers "how many more keys"; headroom answers "how much slack", and on a
structured population (shared prefixes) the two can disagree sharply.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Callable, Iterable, Optional

# The declaration: what `Store.add_memory` mints (uuid4().hex[:12]).
ID_LENGTH = 12
DECLARED_FORM = re.compile(r"^[0-9a-f]{12}$")
DECLARED = "uuid4().hex[:12] — 12 lowercase hex characters"


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

    Two biases travel with it, and they make the verdicts unequal:
      · positions are treated as independent, which shared prefixes INFLATE;
      · a plug-in entropy from n samples cannot see an alphabet wider than n, which
        DEFLATES it — reported as `positions_saturated`.
    So `keys < threshold` is sound; `keys >= threshold` is the weaker claim.
    """
    if not keys:
        return {"space": 1.0, "positions_saturated": 0}
    space, saturated = 1.0, 0
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
    return {"space": space, "positions_saturated": saturated}


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


def headroom(keys: list[str], length: int = ID_LENGTH) -> dict:
    """How much slack the declared fold has, with no statistical model involved: the
    longest truncation that would ALREADY merge the keys in front of you.

    `headroom_chars` is how many characters you could drop before that happens. It
    answers a different question from the population threshold — "how much slack" versus
    "how many more keys" — and on structured keys the two disagree: a huge, well-past-
    threshold population of paths sharing a 20-character prefix reads perfectly safe by
    population and sits one character from collapsing by headroom.
    """
    worst: Optional[int] = None
    for L in range(length - 1, 0, -1):
        if fold_cost(keys, lambda k, L=L: k[:L])["keys_lost"] > 0:
            worst = L
            break
    return {"collides_at_length": worst,
            "headroom_chars": (length - worst) if worst is not None else length,
            "declared_length": length}


def identifier_contract(store) -> dict:
    """The declaration plus what it costs on this store's own keys, measured on call."""
    keys = [r["id"] for r in store.db.execute("SELECT id FROM memory")]
    off_form = [k for k in keys if not DECLARED_FORM.match(k or "")]
    cost = fold_cost(keys, lambda k: k[:ID_LENGTH])
    space = measured_space(keys, ID_LENGTH)
    verdict = fold_verdict(cost, space, population_threshold(space["space"]))
    return {"declared": DECLARED, "keys": len(keys),
            "off_form": off_form[:20], "off_form_count": len(off_form),
            "cost": cost, "space": round(space["space"], 1),
            "positions_saturated": space["positions_saturated"],
            **verdict, **headroom(keys)}
