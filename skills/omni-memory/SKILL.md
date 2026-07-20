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
omni-memory map               # (re)build the knowledge graph
omni-memory ui [--port]       # open the local dashboard (graph + memory docs)
omni-memory recall <query>    # query memory instead of grepping
omni-memory branches          # git topology + per-branch memory
omni-memory remember "<text>" [--kind decision|fact|flow|gotcha|todo]
omni-memory forget <id>       # archive a stale memory
omni-memory install           # wire hooks + skill into this agent
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

## Branch awareness
Memory is scoped to the current git branch + its base. On merge, that branch's
memories roll into the base; abandoned branches are archived. `branch-aware`
can be toggled off to see all branches at once.

## Reporting to the user
When they ask what's remembered, run `recall`/`branches` or point them at
`omni-memory ui` for the dashboard (Memory docs · Graph · Repo Graph).
