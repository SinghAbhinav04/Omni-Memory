"""Digest — render the store into a human-readable MEMORY.md "knowledge base".

This is the persistent artifact: always-loadable docs for the agent (Antigravity
artifact / Claude Code @-reference) and browsable by humans. Regenerated on every
capture so it never drifts from the store.
"""
from __future__ import annotations

import time
from pathlib import Path

from .store import Store

DIGEST_NAME = "MEMORY.md"
# Section order + headings modeled on work/docs README reading-order taxonomy.
_ORDER = ["decision", "concept", "flow", "event", "endpoint", "db",
          "component", "gotcha", "assumption", "fact", "todo"]
_HEADINGS = {
    "decision": "Architecture & Decisions", "concept": "Concepts / Glossary",
    "flow": "Flows", "event": "Events (Kafka contracts)",
    "endpoint": "API map (endpoint → service → repo)", "db": "Database",
    "component": "Reusable components", "gotcha": "Gotchas / Known issues",
    "assumption": "Assumptions (verify)", "fact": "Facts", "todo": "TODOs",
}


def write_digest(store: Store) -> Path:
    """Render all active memories into `.omni-memory/MEMORY.md`, grouped by branch
    then by kind (decisions first, todos last). This is the human/agent-readable
    knowledge base; it's regenerated on every capture/edit so it never drifts."""
    mems = store.memories(status="active", limit=2000)
    by_branch: dict[str, dict[str, list[dict]]] = {}
    for m in mems:
        by_branch.setdefault(m["branch"], {}).setdefault(m["kind"], []).append(m)

    lines = [
        "# Project Memory (OmniMemory)",
        "",
        f"_Auto-generated {time.strftime('%Y-%m-%d %H:%M')} · {len(mems)} active "
        "memories · do not edit by hand; use `omni-memory remember/forget`._",
        "",
        "> Agents: treat this as verified project truth. Cite `[id]`s you use. "
        "If something isn't here or in the code, say \"not in memory\" — don't invent.",
        "",
    ]
    default = store.get_meta("default_branch", "main")
    for branch in sorted(by_branch, key=lambda b: (b != default, b)):
        merged = store.db.execute(
            "SELECT status FROM branches WHERE name=?", (branch,)).fetchone()
        tag = f" _(merged)_" if merged and merged["status"] == "merged" else ""
        lines.append(f"## `{branch}`{tag}")
        for kind in _ORDER:
            items = by_branch[branch].get(kind)
            if not items:
                continue
            lines.append(f"\n### {_HEADINGS.get(kind, kind.title())}")
            for m in items:
                where = f"  — `{'`, `'.join(m['files'][:3])}`" if m["files"] else ""
                flag = " _(inferred)_" if m.get("source") == "inferred" else ""
                lines.append(f"- {m['text']}{flag}  `[{m['id']}]`{where}")
        lines.append("")
    text = "\n".join(lines)
    out = store.dir / DIGEST_NAME
    out.write_text(text)
    return out
