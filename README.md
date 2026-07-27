# ◇ OmniMemory

**The memory & context layer for coding agents** — persistent, branch-aware,
git-anchored, fully local. Your AI stops forgetting between chats and stops
hallucinating architecture it never verified.

> Plugs into **Claude Code** and **Antigravity** (more IDEs coming). Toggle with
> `/omni-memory`. Browse everything in a minimalist local dashboard.

```bash
python -m pip install -e .     # or: pip install omni-memory  (once published)
omni-memory install            # wire it into Claude Code
omni-memory build              # one-time: seed memory from the repo (optional)
omni-memory status
```

The core is **zero-dependency** (Python stdlib + SQLite) and runs with **no API
key**. Set `GEMINI_API_KEY` (or Anthropic/OpenAI) only if you want the AI-written
build pass and artifacts.

## What it does
- **Remembers** decisions, facts, request/data flows, gotchas — automatically at
  the end of each session, and on demand.
- **Branch-aware** — memory is scoped to your git branch; tracks branch creator,
  timeline, and merge status. Merged branches roll into the base.
- **Enforced** — injects a *VERIFIED PROJECT MEMORY* block into prompts and makes
  the agent cite what it used, or admit "not in memory" instead of inventing.
- **Relevant** — an IDF-tiered ranker surfaces the few memories that actually
  match your prompt + the files in play, instead of dumping everything.
- **Self-checking** — `omni-memory check` re-anchors memories against git and
  flags any whose files changed since they were written as ⚠ stale.
- **Clean** — an extraction-noise filter keeps aspirational prose and doc
  boilerplate out of the store.
- **Graph + dashboard** — `omni-memory ui` opens a local UI: browsable memory
  docs, the knowledge graph, and the repo/branch graph.

## Commands
```
omni-memory status | on | off | branch-aware
omni-memory build          # one-time: AI-written facts from the repo + docs
omni-memory ui             # local dashboard (graph + memory docs + repo graph)
omni-memory map            # (re)build the knowledge graph
omni-memory check          # re-anchor vs git; flag stale memories
omni-memory recall <q>     # query memory instead of grepping
omni-memory branches       # git topology + per-branch memory
omni-memory remember "…" [--kind decision|fact|flow|gotcha|todo|…]
omni-memory forget <id>
omni-memory digest         # (re)write the MEMORY.md knowledge base
omni-memory artifact [apimap|linkup|all]   # AI-written cross-reference docs
omni-memory key <gemini|anthropic|openai>  # store a model key securely (chmod 600)
omni-memory install [--platform claude-code|antigravity]
```

## How it works
```
CAPTURE (session + git) → STORE (SQLite, branch-tagged) → RANK + INJECT + ENFORCE
       → CHECK (staleness vs git) → VISUALIZE (dashboard)
```
Capture fires from deterministic harness events (`UserPromptSubmit` → inject,
`SessionEnd` → capture) — never left to the agent's goodwill. Local-first, no
cloud, no paid data. See [`PLAN.md`](PLAN.md) for the full architecture and roadmap.

## Status
Core is in: store · git provenance · branch-aware scoping · capture/inject/enforce
· IDF relevance ranker · staleness anchoring · noise filter · `map` · dashboard
(knowledge graph + repo/branch graph). Roadmap: Antigravity via MCP, tree-sitter
AST code graph, runtime request-flow capture. See `PLAN.md`.

## License
MIT © 2026 Abhinav Singh
