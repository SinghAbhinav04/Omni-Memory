---
name: omni-memory
description: Persistent, branch-aware project memory for coding agents. Use when the user types /omni-memory (status/on/off/config/check/ui/recall/gain/branches/forget/bind), asks what the project "remembers", wants the agent to use verified memory instead of guessing, or wants to browse the memory dashboard/graph. Also: at the start of a task, pull relevant memory; at the end, capture decisions/flows/gotchas.
---

# OmniMemory — memory & context layer

Persistent, self-updating, **branch-aware** memory that lives in `.omni-memory/`
in the project. It remembers decisions, facts, request/data flows, gotchas and
git provenance, and **enforces** that you use verified memory rather than
hallucinating architecture.

## Commands (run via the `omni-memory` CLI)
```
omni-memory status            # layer state, branch, memory counts
omni-memory doctor            # diagnose setup (git, store, graph, hooks, AGENTS.md, AI)
omni-memory config [k] [v]    # show/change settings (inject_mode, branch_aware, budgets…)
omni-memory on | off          # enable/disable the memory layer
omni-memory bind [claude-code|opencode|antigravity|cursor|windsurf]  # onboarding (auto-detects)

omni-memory recall <query>    # query memory instead of grepping
omni-memory inject "<q>"      # the VERIFIED PROJECT MEMORY block for a task
omni-memory remember "<text>" [--kind …] [--global] [--verified|--inferred]  # evidence = trust/prune weight
omni-memory used <id> …       # cite memories you relied on (+ re-verify at point of use)
omni-memory forget <id>       # archive a stale memory
omni-memory lock <id> [--off] # pin as constitutional (never decays or gets evicted)

omni-memory build             # bootstrap memory + docs from the repo
omni-memory check [--graph]   # re-anchor vs git; flag ⚠ stale (--graph rebuilds the graphs)
omni-memory capture [--prompt build|session]   # ingest extraction JSON / print the prompt
omni-memory gc [--dry-run] [--purge] [--restore <id|branch>]   # quarantine dead/false memory
omni-memory flush [--scope all|memory|graph] [-y]  # wipe store to rebuild from scratch

omni-memory systemmap [-o F]  # self-contained HTML architecture map, citation-backed
omni-memory ui [--port]       # local dashboard — auto-updates itself (watches git + files)
omni-memory gain [--history]  # tokens SAVED by memory vs re-reading, + footprint + tuning
omni-memory branches          # git topology + per-branch memory

omni-memory share [--status] · sync           # team memory over git
omni-memory snapshot [--out|--in FILE] [--global]   # portable JSON, both directions
```

## Agent-driven extraction (runs INSIDE this agent — no API key)
OmniMemory prefers to let *you, the agent* do the thinking; it just stores the
result. No external model key is required.

- **`/omni-memory build`** (one-time bootstrap): run `omni-memory capture --prompt build`
  to get the instructions, study the repo yourself, and pipe your JSON output to
  `omni-memory capture`. Example:
  `omni-memory capture --prompt build` → (you analyze the code) → `echo '<your JSON>' | omni-memory capture`.
  `omni-memory build` writes the api-map/linkup docs when a model key is set.
- **End of a task:** run `omni-memory capture --prompt session`, extract the durable
  memories from what just happened, and `echo '<JSON>' | omni-memory capture`.

**Capture the memories yourself at the end of a task** — the SessionEnd hook's
automatic capture is a FREE heuristic pass (no `claude -p`, to save tokens), so the
high-quality extraction must come from you, the agent, running
`omni-memory capture --prompt session` → `omni-memory capture`. Set an API key or
`OMNI_HEADLESS_LLM=1` only if you want the hook to do an LLM pass unattended.

## How to use it in a session
Memory is **pulled on demand**, not force-fed into every prompt (that wastes
tokens). It's kept fresh for you — refreshed at session start and after commits —
so you can rely on it as a current source of truth.
1. **Pull when you need it:** before assuming any architecture, or when you need a
   decision/flow/gotcha/endpoint/DB-schema, run
   `omni-memory inject "<what you need>"` and treat the returned **VERIFIED PROJECT
   MEMORY** block as ground truth — cite the `[id]`s you rely on. (`omni-memory
   recall "<q>"` is a lighter search.) If something isn't in memory or the code,
   say "not in memory"; don't invent endpoints, params, DB tables, or flows.
2. **During:** if you learn a durable decision/flow/gotcha, run
   `omni-memory remember "<one sentence>" --kind <kind>`.
3. **End of task:** extract memory from what happened and pipe JSON to
   `omni-memory capture` (array of `{kind,text,files,symbols}`). The SessionEnd
   hook does this automatically once installed.

## Cross-IDE context + pull (Claude Code, Antigravity, …)
OmniMemory maintains a canonical **`AGENTS.md`** at the repo root — a delimited
managed block that every AI IDE reads as project context on session start. It
carries the **pull instructions** plus a compact snapshot, so a fresh session in
*any* IDE knows to fetch memory on demand. It's rendered from the persisted
`.omni-memory/` store (no re-reading the whole repo) and refreshed on every
capture/build, by the dashboard watcher, and by the `SessionStart` hook.

**Injection modes** (`omni-memory config inject_mode <mode>`): `session` (default) seeds
the ranked block once at session start, then you pull with `omni-memory inject`;
`auto` injects into every prompt (enforced, costs more tokens); `manual` never
auto-injects. `omni-memory bind antigravity` writes AGENTS.md too.

## Branch awareness
Memory is scoped to the current git branch + its base. On merge, that branch's
memories roll into the base; abandoned branches are archived. `config branch_aware off`
turns it off to see all branches at once.

## Reporting to the user
When they ask what's remembered, run `recall`/`branches` or point them at
`omni-memory ui` for the dashboard (Memory docs · Graph · Repo Graph).
