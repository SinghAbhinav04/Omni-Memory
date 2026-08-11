"""Canonical, IDE-agnostic project context — the root `AGENTS.md`.

Every major AI IDE (Claude Code, Antigravity, Cursor, …) reads an `AGENTS.md` at
the repo root as standing project context at the start of a session. OmniMemory
maintains a delimited block inside that file so a fresh session in ANY IDE
auto-loads verified memory — and it costs nothing to re-derive, because the
content is rendered from the already-persisted `.omni-memory/` store rather than
by re-reading the whole repo. The rest of the file (anything outside the block)
is the user's and is left untouched.
"""
from __future__ import annotations

import time
from pathlib import Path

from .store import Store

AGENTS_NAME = "AGENTS.md"
START = "<!-- OMNI-MEMORY:START — auto-generated; edit outside this block only -->"
END = "<!-- OMNI-MEMORY:END -->"

# Which memory kinds are worth surfacing inline (highest-signal first) and how
# many of each to show. Kept small: AGENTS.md is standing context some IDEs load
# every session, so inlining a lot here is a recurring token cost — the agent can
# always pull the rest on demand via `omni-memory inject`/`recall`.
_INLINE = [("decision", 4), ("gotcha", 3), ("flow", 2), ("endpoint", 2)]


def render_block(store: Store, root: Path) -> str:
    """The managed block: usage rules + a compact snapshot of top memories."""
    default = store.get_meta("default_branch", "main")
    mems = store.memories(status="active", limit=2000)
    by_kind: dict[str, list[dict]] = {}
    for m in mems:
        by_kind.setdefault(m["kind"], []).append(m)

    lines = [
        START,
        "## Project memory (OmniMemory)",
        "",
        f"_Auto-generated {time.strftime('%Y-%m-%d %H:%M')} · {len(mems)} verified "
        f"memories · default branch `{default}`._",
        "",
        "This project has a persistent, branch-aware memory layer that stays fresh "
        "automatically (rebuilt at session start and after commits). **It is a "
        "reliable source of truth — pull it instead of guessing.** It is NOT "
        "force-fed into every prompt (that wastes tokens); fetch it when relevant.",
        "",
        "- **Pull on demand:** before assuming any architecture — or when you need a "
        "decision, flow, gotcha, endpoint, or DB schema — run "
        "`omni-memory inject \"<what you need>\"` for the ranked **VERIFIED PROJECT "
        "MEMORY** block, and cite the `[id]`s you rely on. (`omni-memory recall "
        "\"<q>\"` is a lighter search.)",
        "- The snapshot below orients you; treat it and pulled memory as verified "
        "truth. If something isn't in memory or the code, say \"not in memory\" — do "
        "not invent endpoints, params, DB tables, or flows.",
        "- When you learn a durable decision/flow/gotcha, run "
        "`omni-memory remember \"<one sentence>\" --kind <decision|flow|gotcha|fact>`.",
        "- Full knowledge base: `.omni-memory/MEMORY.md` · dashboard: `omni-memory ui`.",
        "",
    ]
    shown = False
    for kind, n in _INLINE:
        items = by_kind.get(kind)
        if not items:
            continue
        shown = True
        label = {"decision": "Key decisions", "gotcha": "Gotchas",
                 "flow": "Flows", "endpoint": "API map"}.get(kind, kind.title())
        lines.append(f"**{label}**")
        for m in items[:n]:
            where = f" — `{m['files'][0]}`" if m.get("files") else ""
            lines.append(f"- {m['text']}  `[{m['id']}]`{where}")
        lines.append("")
    if not shown:
        lines.append("_No memories captured yet — run `omni-memory build` to "
                     "bootstrap, or work a session with the layer enabled._")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def write(store: Store, root: Path) -> Path:
    """Insert/refresh OmniMemory's managed block in the repo-root AGENTS.md,
    preserving anything the user put outside it. Creates the file if absent."""
    block = render_block(store, root)
    path = root / AGENTS_NAME
    if path.exists():
        old = path.read_text(encoding="utf-8", errors="ignore")
        if START in old and END in old:
            pre = old.split(START, 1)[0]
            post = old.split(END, 1)[1]
            new = f"{pre}{block}{post}"
        else:  # user has an AGENTS.md but no managed block yet → append ours
            sep = "" if old.endswith("\n\n") else ("\n" if old.endswith("\n") else "\n\n")
            new = f"{old}{sep}{block}\n"
    else:
        new = (f"# AGENTS.md\n\n_Standing instructions for AI coding agents in "
               f"this repo._\n\n{block}\n")
    from .store import atomic_write            # atomic: several processes write this
    atomic_write(path, new)
    return path
