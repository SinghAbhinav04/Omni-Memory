---
name: omni-memory
description: Persistent, branch-aware project memory for coding agents. Use when the user types /omni-memory (status/on/off/branch-aware/map/ui/recall/why/branches/forget/install), asks what the project "remembers", wants the agent to use verified memory instead of guessing, or wants to browse the memory dashboard/graph. Also: at the start of a task, pull relevant memory; at the end, capture decisions/flows/gotchas.
---

# OmniMemory — memory & context layer

Persistent, self-updating, **branch-aware** memory that lives in `.omni-memory/`
in the project. It remembers decisions, facts, request/data flows, gotchas and
git provenance, and **enforces** that you use verified memory rather than
hallucinating architecture.

## Commands (run via the `omni-memory` CLI)
```
omni-memory status            # layer state, branch, memory counts
omni-memory on | off          # enable/disable the memory layer
omni-memory branch-aware      # toggle branch-scoped memory
omni-memory map               # (re)build the knowledge graph + code graph (tree-sitter)
omni-memory check             # re-anchor memories vs git; flag ⚠ stale (symbol-level)
omni-memory ui [--port]       # open the local dashboard — auto-updates itself
                              #   (watches git + files; branches, graph, code
                              #    graph & staleness refresh live, no re-running)
omni-memory recall <query>    # query memory instead of grepping
omni-memory branches          # git topology + per-branch memory
omni-memory remember "<text>" [--kind decision|fact|flow|gotcha|todo]
omni-memory forget <id>       # archive a stale memory
omni-memory used <id> …       # cite memories you relied on (improves ranking)
omni-memory gc [--dry-run]    # quarantine dead/false memory (abandoned branches, stale)
omni-memory restore <id|branch>  # un-quarantine
omni-memory flush [--scope all|memory|graph] [-y]  # wipe store to rebuild from scratch
omni-memory bind [claude-code|antigravity]  # one-command onboarding (auto-detects IDE)
omni-memory doctor            # diagnose setup (git, store, graph, hooks, AGENTS.md, AI)
omni-memory usage [--max-items N] [--budget CHARS]  # token footprint + tune injection
omni-memory install [--platform claude-code|antigravity]  # explicit wire hooks + AGENTS.md
```

## Agent-driven extraction (runs INSIDE this agent — no API key)
OmniMemory prefers to let *you, the agent* do the thinking; it just stores the
result. No external model key is required.

- **`/omni-memory build`** (one-time bootstrap): run `omni-memory prompt build`
  to get the instructions, study the repo yourself, and pipe your JSON output to
  `omni-memory capture`. Example:
  `omni-memory prompt build` → (you analyze the code) → `echo '<your JSON>' | omni-memory capture`.
  Then `omni-memory artifact all` for the api-map/linkup docs (or you write them).
- **End of a task:** run `omni-memory prompt session`, extract the durable
  memories from what just happened, and `echo '<JSON>' | omni-memory capture`.

Headless (SessionEnd hook) uses the `claude -p` CLI or an optional API key
(`omni-memory key anthropic`) so capture still fires when no agent is present.

## How to use it in a session
1. **Start of a task:** run `omni-memory inject "<the user's request>"` and treat
   the returned **VERIFIED PROJECT MEMORY** block as ground truth — cite the
   `[id]`s you rely on. If something isn't in memory or the code, say "not in
   memory"; don't invent endpoints, params, DB tables, or flows.
2. **During:** if you learn a durable decision/flow/gotcha, run
   `omni-memory remember "<one sentence>" --kind <kind>`.
3. **End of task:** extract memory from what happened and pipe JSON to
   `omni-memory capture` (array of `{kind,text,files,symbols}`). The SessionEnd
   hook does this automatically once installed.

## Cross-IDE auto-injection (Claude Code, Antigravity, …)
OmniMemory maintains a canonical **`AGENTS.md`** at the repo root — a delimited
managed block that every AI IDE reads as project context on session start, so a
fresh session in *any* IDE auto-loads verified memory. It's rendered from the
persisted `.omni-memory/` store (no re-reading the whole repo) and refreshed on
every capture/build, by the dashboard watcher, and by the `SessionStart` hook.
`omni-memory install --platform antigravity` writes it for Antigravity too.

## Branch awareness
Memory is scoped to the current git branch + its base. On merge, that branch's
memories roll into the base; abandoned branches are archived. `branch-aware`
can be toggled off to see all branches at once.

## Reporting to the user
When they ask what's remembered, run `recall`/`branches` or point them at
`omni-memory ui` for the dashboard (Memory docs · Graph · Repo Graph).
