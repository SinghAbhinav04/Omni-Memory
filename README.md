# ◇ OmniMemory

**The memory & context layer for coding agents** — persistent, branch-aware,
git-anchored, fully local. Your AI stops forgetting between chats and stops
hallucinating architecture it never verified.

> Plugs into **Claude Code** and **Antigravity** (more IDEs coming). Toggle with
> `/omni-memory`. Browse everything in a minimalist local dashboard.

```bash
pip install -e .            # or: pip install omni-memory  (once published)
omni-memory install        # wire it into Claude Code
omni-memory status
```

## What it does
- **Remembers** decisions, facts, request/data flows, gotchas — automatically at
  the end of each session, and on demand.
- **Branch-aware** — memory is scoped to your git branch; tracks branch creator,
  timeline, and merge status. Merged branches roll into the base.
- **Enforced** — injects a *VERIFIED PROJECT MEMORY* block into prompts and makes
  the agent cite what it used, or admit "not in memory" instead of inventing.
- **Graph + dashboard** — `omni-memory ui` opens a local UI: browsable memory
  docs, the knowledge graph, and the repo/branch graph.

## Commands
```
omni-memory status | on | off | branch-aware
omni-memory map            # build the knowledge graph
omni-memory ui             # local dashboard (graph + memory docs + repo graph)
omni-memory recall <q>     # query memory instead of grepping
omni-memory branches       # git topology + per-branch memory
omni-memory remember "…" [--kind decision|fact|flow|gotcha|todo]
omni-memory forget <id>
omni-memory install [--platform claude-code|antigravity]
```

## How it works
```
CAPTURE (session + git) → STORE (SQLite, branch-tagged) → SERVE (dashboard/MCP)
       → INJECT + ENFORCE (verified memory in every prompt) → VISUALIZE
```
Local-first, no cloud, no paid data. See [`PLAN.md`](PLAN.md) for the full
architecture and roadmap.

## Status
P0 (core: store · git provenance · branch-aware · capture/inject/enforce ·
`map` · dashboard) is in. Roadmap: Antigravity via MCP, graphify AST code graph,
runtime request-flow capture, React dashboard. See `PLAN.md`.

## Credits
Builds on the great MIT-licensed work of **graphify** and **supermemory** — see
[`NOTICE`](NOTICE).

## License
MIT
