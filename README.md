# ◇ OmniMemory

**The memory & context layer for coding agents** — persistent, branch-aware,
git-anchored, fully local. Your AI stops forgetting between chats and stops
hallucinating architecture it never verified.

> Works with **Claude Code**, **OpenCode**, **Antigravity**, **Cursor** & **Windsurf** (any AGENTS.md-aware IDE). Toggle with
> `/omni-memory`. Browse everything in a minimalist local dashboard.

## Install

**pip** (recommended — gives you the `omni-memory` CLI):
```bash
pip install omni-memory-agent
```

**Claude Code plugin** (session hooks + skill, no pip needed for injection):
```
/plugin marketplace add SinghAbhinav04/Omni-Memory
/plugin install omni-memory@singhabhinav
```

The core is **zero-dependency** (Python stdlib + SQLite) and runs with **no API key**.
On Python ≥3.10 it auto-installs tree-sitter for the exact multi-language code graph.
Without it (e.g. the plugin, or Python 3.9) a stdlib backend still graphs Python (`ast`)
and JS/TS (regex) — approximate, but real.

## Quick start — two steps people mix up

They do **different** things:

| Command | What it does |
|---|---|
| **`omni-memory build`** | **Reads your repo and creates the content** — captures decisions/flows/gotchas, builds the code graph, and writes the docs (`MEMORY.md`, `api-map.md`, `linkup.md`). |
| **`omni-memory bind`** | **Connects it to your IDE** — installs the session hooks and writes the cross-IDE `AGENTS.md`. It does **not** create memory or docs. |

First run, in order:

```bash
pip install omni-memory-agent

omni-memory build      # 1. build memory + DOCS from your repo   ← the content
omni-memory bind       # 2. wire it into your IDE (hooks + AGENTS.md)
omni-memory ui         # 3. browse memory, docs, and the code graph
omni-memory doctor     # anytime: verify the setup is healthy
```

> **About `build`:** it reads your codebase with an agent/LLM. Run it **inside an AI IDE**
> (so the agent does the analysis) or set a model key (`omni-memory key gemini`). Without
> either, it still builds the **code graph + heuristic docs** — just no AI-written facts.

After that it's automatic: memory injects into every prompt and captures itself when a
session ends.

## What it does
- **Remembers** decisions, facts, request/data flows, gotchas — automatically at
  the end of each session, and on demand.
- **Branch-aware** — memory is scoped to your git branch; tracks branch creator,
  timeline, and merge status. Merged branches roll into the base.
- **Pull-based (token-light)** — memory isn't force-fed into every prompt. It's
  seeded once at session start and the agent *pulls* it on demand
  (`omni-memory inject "<q>"`) as verified ground truth — cite `[id]`s or admit
  "not in memory". Kept fresh (session start + after commits) so it's reliable.
  Prefer per-prompt enforcement? `omni-memory inject-mode auto`.
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
# setup
omni-memory build            # build memory + DOCS from the repo (MEMORY.md, api-map, linkup)
omni-memory bind [ide]       # wire an IDE: session hooks + AGENTS.md (auto-detects)
omni-memory ui               # dashboard: overview · memory · docs · code & repo graph
omni-memory doctor           # diagnose setup (git, store, graph, hooks, AGENTS.md, AI)
omni-memory status | on | off | branch-aware

# using memory
omni-memory recall <q>                       # search memory instead of grepping
omni-memory remember "…" [--kind …] [--global]   # add one by hand (--global = every project)
omni-memory forget <id> · used <id>… · restore <id|branch>
omni-memory branches                         # git topology + per-branch memory

# keeping it fresh
omni-memory map              # (re)build the knowledge + tree-sitter code graph
omni-memory check            # re-anchor vs git; flag ⚠ stale memories (symbol-level)
omni-memory gc [--dry-run] [--purge]         # quarantine dead/false memory
omni-memory usage [--max-items N] [--budget C]   # per-prompt token footprint + tune

# sharing / reset
omni-memory export [file] [--global]         # portable snapshot (commit it to share)
omni-memory import [file] [--global]         # load an export (idempotent, ids preserved)
omni-memory flush [--scope all|memory|graph] [-y]    # wipe to rebuild from scratch
omni-memory key <gemini|anthropic|openai>    # store a model key for the AI build pass
```
`bind` is the friendly setup command; `install [--platform …]` is the explicit form.

## How it works
```
CAPTURE (session + git) → STORE (SQLite, branch-tagged) → RANK + INJECT + ENFORCE
       → CHECK (staleness vs git) → VISUALIZE (dashboard)
```
Capture fires from deterministic harness events (`PreCompact` + `SessionEnd` →
capture) — never lost to compaction. Memory is seeded at `SessionStart` and pulled
on demand thereafter (`inject-mode`: session·auto·manual). Local-first, no cloud,
no paid data. See [`PLAN.md`](PLAN.md) for the full architecture and roadmap.

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
**Proprietary © 2026 Abhinav Singh. All rights reserved.** OmniMemory is
*not* open source — you may install and run the official published package, but
not copy, modify, redistribute, or claim it as your own. See [`LICENSE`](LICENSE).
