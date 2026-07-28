# ◇ OmniMemory

**The memory & context layer for coding agents** — persistent, branch-aware,
git-anchored, fully local. Your AI stops forgetting between chats and stops
hallucinating architecture it never verified.

> Plugs into **Claude Code** and **Antigravity** (more IDEs coming). Toggle with
> `/omni-memory`. Browse everything in a minimalist local dashboard.

## Install

**Option A — Claude Code plugin (no pip needed).** The zero-dependency engine
rides along inside the plugin, so this is all it takes:

```
/plugin marketplace add SinghAbhinav04/Omni-Memory
/plugin install omni-memory@singhabhinav
```

That wires the skill + the capture/inject hooks automatically. Just work — memory
injects on every prompt and updates itself when a session ends.

**Option B — pip (gives you the `omni-memory` CLI everywhere).**

```bash
pip install omni-memory-agent      # or, from a clone: python -m pip install -e .
omni-memory install                # wire it into Claude Code (the CLI is `omni-memory`)
omni-memory build                  # one-time: seed memory from the repo (optional)
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
- **Relevant (context-aware)** — a BM25F ranker (symbol/file/prose field
  weighting) surfaces the few memories that match your prompt, boosts those
  anchored to code **near what you're editing** (via the call graph), and lifts
  memories the agent has actually cited before. No embeddings, no key.
- **Self-checking (symbol-level)** — `omni-memory check` builds a tree-sitter
  code graph and flags a memory ⚠ stale only when *its* symbol — or a symbol that
  calls it — actually changed in git, not just because the file was touched.
  Falls back to file-level when tree-sitter isn't present.
- **Clean** — an extraction-noise filter keeps aspirational prose and doc
  boilerplate out of the store.
- **Self-cleaning** — memories stranded on **abandoned branches** (deleted
  unmerged, or long dormant) and long-stale/uncited memories are auto-quarantined
  (reversible), so false memory doesn't live forever. Memories the agent keeps
  citing are shielded. `gc --dry-run` previews; hard-delete stays human-gated.
- **Graph + dashboard** — `omni-memory ui` opens a local UI: browsable memory
  docs, the knowledge graph, and the repo/branch graph.

## Commands
```
omni-memory status | on | off | branch-aware
omni-memory build          # one-time: AI-written facts from the repo + docs
omni-memory ui             # local dashboard (graph + memory docs + repo graph)
omni-memory map            # (re)build the knowledge graph + tree-sitter code graph
omni-memory check          # re-anchor vs git; flag stale memories (symbol-level)
omni-memory recall <q>     # query memory instead of grepping
omni-memory branches       # git topology + per-branch memory
omni-memory remember "…" [--kind decision|fact|flow|gotcha|todo|…]
omni-memory forget <id>
omni-memory used <id> …    # record a citation (feeds the ranker)
omni-memory gc [--dry-run] [--purge]   # quarantine dead/false memory; purge is human-gated
omni-memory restore <id|branch>        # un-quarantine
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
· context-aware ranker (BM25F + code-graph proximity + citation feedback) ·
symbol-level staleness · noise filter · **memory hygiene** (abandoned-branch &
false-memory auto-quarantine, human-gated purge) · **tree-sitter
code graph** (Python/JS/TS, with a stdlib-`ast` fallback) · dashboard (knowledge
graph + repo/branch graph). tree-sitter installs automatically on Python ≥3.10;
on 3.9 the base install still works and graphs Python via `ast`. Roadmap:
Antigravity via MCP, more languages, runtime request-flow capture. See `PLAN.md`.

## License
MIT © 2026 Abhinav Singh
