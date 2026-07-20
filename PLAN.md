# OmniMemory — the memory & context layer for coding agents

> **One line:** the memory layer that actually remembers your *architecture* —
> branch-aware, flow-aware, git-anchored, fully local, across every chat and IDE.

Your AI coding agent forgets everything between chats, re-greps the same files,
and hallucinates architecture it never verified. OmniMemory is a persistent,
self-updating, graph-backed memory that plugs into Claude Code and Antigravity
(more IDEs later), keeps learning as you work, and **forces the agent to use
verified project memory instead of guessing** — with a minimalist local
dashboard to browse it all.

---

## 0. Positioning & why it wins

We stand on two excellent MIT parents and go past both by owning the
intersection neither covers:

| | graphify | supermemory | **OmniMemory** |
|---|---|---|---|
| Local code knowledge graph (AST) | ✅ | — | ✅ (reuse) |
| Evolving conversational memory | — | ✅ | ✅ (reimpl. local) |
| Contradiction handling + forgetting | — | ✅ | ✅ |
| Multi-IDE skill install + MCP | ✅ | partial | ✅ |
| **Branch-aware memory + git provenance** | — | — | ✅ ⭐ |
| **Runtime request-flow capture (real params/returns)** | static only | — | ✅ ⭐ |
| **Enforced usage (anti-hallucination)** | — | — | ✅ ⭐ |
| **Local dashboard (graph + readable memory docs + repo graph)** | graph.html | cloud UI | ✅ ⭐ |
| Fully local / free | ✅ | leans cloud | ✅ |

The three ⭐ + the local dashboard are the reason-to-exist and the star magnet.
Viral demo: *"switch to my feature branch → it already knows what changed there,
who started it, and whether it merged."*

---

## 1. Parents & reuse map (both MIT — attributed in NOTICE)

**Reuse from graphify (Python):**
- `extract.py` — tree-sitter AST → code nodes/edges (deterministic, no LLM)
- `build.py` / `cluster.py` / `analyze.py` — NetworkX graph, Leiden communities, god nodes
- `export.py` / `tree_html.py` — graph.json / graph.html / svg
- `callflow_html.py` — Mermaid architecture & call-flow diagrams
- `pg_introspect.py` — Postgres schema context; `manifest_ingest.py` — deps
- `serve.py` — MCP stdio server; `install.py` — multi-IDE skill wiring
- `watch.py` — incremental re-map on file change; `global_graph.py` — persistence
- `security.py` / `validate.py` — hardening + schema validation

**Reuse from supermemory:**
- `@supermemory/memory-graph` (React) — the interactive connected-graph component
- `apps/mcp` — MCP tool shapes / OAuth patterns
- Memory-engine *design*: fact extraction, contradiction resolution, temporal
  forgetting, profiles (we reimplement in Python, local, no cloud)

**Build new (the wedge):** `session_memory.py`, `inject.py` (+enforcement),
`branch.py`, `gitmeta.py`, `runtime.py`, the FastAPI `api.py`, the `web/` dashboard.

---

## 2. Commands (`/omni-memory …`) — modular, user-friendly

Everything is a toggle/subcommand; nothing is forced on.

```
/omni-memory                 status + turn the layer on/off for this project
/omni-memory on | off        enable/disable memory injection
/omni-memory branch-aware    toggle branch-scoped memory (on by default)
/omni-memory map             (re)build the code/knowledge graph      [graphify]
/omni-memory ui              launch the local dashboard (browser)
/omni-memory graph           open the interactive graph view
/omni-memory recall <q>      query memory instead of grepping
/omni-memory why <x>         trace/explain a connection between two things
/omni-memory branches        show the repo git-graph + per-branch memory
/omni-memory flows           show request/call-flow diagrams
/omni-memory forget <id>     drop / archive a stale memory
/omni-memory status          what's remembered, current branch, store size
/omni-memory install         wire into Claude Code + Antigravity
```

---

## 3. Architecture — 5 layers (additions only, nothing rebuilt)

```
 CAPTURE  ──►  STORE  ──►  SERVE  ──►  INJECT+ENFORCE  ──►  VISUALIZE
 (static +     (SQLite +   (MCP for    (prompt hook +      (local dashboard:
  chat +       graph,      agent,      citation rules)      graph · docs ·
  runtime +    branch-      HTTP for                        repo-graph ·
  git)         tagged)      the UI)                         flows · timeline)
```

1. **Capture**
   - *Static:* graphify AST graph + DB (`pg_introspect`) + deps + OpenAPI/endpoints
   - *Conversational:* SessionEnd hook → extract decisions / facts / gotchas / flows
     from the transcript + diff (uses the agent's own model)
   - *Runtime (later):* ingest logs / test runs / optional instrumentation → actual
     params, return shapes, success vs failure paths
   - *Git:* `gitmeta.py` → branch creator, timeline, base, merge commit/date, status
2. **Store** — local `.omni-memory/` (SQLite + graph JSON). Every memory tagged
   `branch`, `kind`, `files[]`, `symbols[]`, `status`, `confidence`, `source`,
   linked to commit range. Contradiction-check + supersede + forget.
3. **Serve** — `serve.py` runs **MCP** (for the agent) **+ FastAPI** (for the UI)
   over the same store. `/omni-memory install` writes the per-platform config.
4. **Inject + Enforce** — a prompt hook retrieves the relevant memories (current
   branch ∪ base + files in context + query) and graph subgraph, injects a
   **"VERIFIED PROJECT MEMORY"** block with rules: *use it, cite the memory IDs
   you relied on, and if it's not in memory say so — don't invent.* Agent reports
   used IDs → logged (proves it's using memory; powers the demo + trust).
5. **Visualize** — minimalist monochrome dashboard (see §6).

---

## 4. Data model (SQLite, local)

```
memory(id, branch, kind[decision|fact|flow|gotcha|todo|api|db|endpoint],
       text, files[], symbols[], commit_range, created, updated,
       status[active|merged|abandoned|superseded], confidence, source,
       supersedes_id)

branches(name, creator, created_at, base_branch, ahead, behind,
         status[active|merged|abandoned], merged_at, merge_commit, into_branch)

commits(sha, branch, author, date, message, files[])

flows(id, name, entry, steps_json[success/failure with params+returns], branch)

graph_nodes / graph_edges  (branch-tagged; from graphify)
memory_commit_link(memory_id, sha)
```

---

## 5. Core loop (v1 = continuous memory + enforcement + branch-aware)

1. **Capture** — SessionEnd hook → structured memories tagged with current git
   branch + touched files; contradiction-check, supersede stale.
2. **Inject** — prompt hook → relevant memories + subgraph → "VERIFIED PROJECT
   MEMORY" block with enforcement instructions.
3. **Branch-aware** — memories filter by branch; git hooks flip `status` on
   branch-switch / merge (merged rolls into base; abandoned is archived).
   `/omni-memory branch-aware` toggles branch-scoping vs global.
4. **Enforce** — agent cites used memory IDs; `querylog` records usage.

---

## 6. Frontend — minimalist local dashboard (monochrome, offline)

Launched by `/omni-memory ui` → FastAPI serves a **bundled** Vite+React SPA on
localhost (works offline; ships pre-built in the wheel).

**Views:**
- **Graph** — interactive connected-knowledge graph (`@supermemory/memory-graph`)
- **Memory** — every stored memory as browsable, searchable "docs"; filter by
  branch / kind / file; click to read full entry + linked files/symbols/commits
- **Repo Graph** — the git DAG (`@gitgraph/js` or d3): `main` trunk + feature
  branches, merge points + dates + **branch creator**; click a commit/branch →
  the memories attached to it (git history *is* the memory index)
- **Flows** — request/call-flow diagrams (graphify Mermaid + runtime later)
- **Timeline** — memory growing over time ("it's alive")

**API:** `/api/memories` (list/search/filter) · `/api/memory/:id` · `/api/graph`
· `/api/branches` · `/api/commits` · `/api/flows`. MCP = agent face, HTTP = UI face.

---

## 7. IDE integration (scope: Claude Code + Antigravity)

- **Claude Code:** a `/omni-memory` skill + hooks in settings.json
  (SessionEnd → capture; UserPromptSubmit/PreToolUse → inject+enforce) + MCP
  server for recall tools.
- **Antigravity:** via the MCP server (same `serve.py`) + its rules/memories
  mechanism.
- `/omni-memory install` writes both (reuse graphify's per-platform writers).
- Extensible to Codex / OpenCode / Gemini CLI / VS Code via the same MCP + skill
  pattern.

---

## 7a. Knowledge-base format — the north star (modeled on real usage)

OmniMemory's job is to **auto-generate and maintain** the kind of AI knowledge
base a senior dev builds by hand. Reference: `Documents/work/docs` (a real
multi-repo BFHL platform kb). We mirror its proven shape:

- **Indexed with a reading order** — `MEMORY.md` opens with architecture →
  concepts → flows → events → api-map → db → components, then running notes.
- **Kinds mapped to that taxonomy:** `decision · concept · flow · event (kafka
  contract) · endpoint (controller→service→repo+params) · db · component ·
  gotcha/known-issue · assumption (verify) · todo · fact`.
- **Confidence markers** — `EXTRACTED / INFERRED / ASSUMPTION` (graphify labels +
  their TODO/ASSUMPTION/Inferred convention).
- **Two killer auto-generated artifacts (P1/P2, from the AST graph + runtime):**
  - **api-map** — every endpoint → controller → service → repo/downstream with
    DTOs/params/headers (replaces hand-written `api-map.md`).
  - **linkup** — the master cross-reference: Mermaid repo-dependency graph,
    controller→service→repo maps, Kafka producer/consumer contracts, shared
    DBs/code (replaces hand-written `linkup.md`).
- **Multi-repo aware** — one memory/graph spanning sibling repos (graphify
  `global_graph`), because real systems are many repos.
- **Mermaid** for dependency/flow diagrams in the dashboard + digest.

## 7b. Autonomy & persistence — why this beats other memory tools

The failure of every memory tool is that **updating** the memory is left to the
agent's goodwill — and agents forget. OmniMemory's rule: **never rely on the
agent to remember to update.** Capture fires from deterministic *harness events*,
and retrieval is always-on.

**Claude Code (hooks = harness-run, not agent-run):**
- `UserPromptSubmit` → `omni-memory inject` (always injects verified memory).
- `SessionEnd` / `Stop` → `omni-memory capture` (extracts from transcript+diff
  via a small model, deterministically — no agent decision needed).
- `PostToolUse(Edit|Write)` → mark graph stale / incremental re-map.
- The skill + `MEMORY.md` digest are always available for @-reference.

**Antigravity (artifacts = always-in-context knowledge):**
- Write the `MEMORY.md` digest as a persistent **artifact** so it's always
  loaded into context (no re-fetch).
- Register OmniMemory as an **MCP server** for deep recall + capture tools.
- Capture on task-complete events where available; else a cadence + the
  staleness nudge below.

**Staleness watchdog (the "keep reminding to update" fix):** the store tracks
`last_capture` + commits since. If stale, the injected block **prepends a
directive** ("⚠ memory N commits stale — capturing now") and the hook runs
capture. Automation is the fix; the nudge is only the fallback. Result: the
knowledge base updates itself as you work, across branches, without you or the
agent having to remember.

## 8. Repo structure

```
omni-memory/
  omni_memory/
    cli.py            # /omni-memory dispatch
    store.py          # SQLite store + schema
    branch.py         # branch-aware memory logic
    gitmeta.py        # git provenance (creator, timeline, merges)
    session_memory.py # SessionEnd capture (transcript+diff → memories)
    inject.py         # prompt injection + enforcement
    runtime.py        # runtime flow capture (P2)
    serve.py          # MCP + FastAPI
    api.py            # HTTP endpoints for the UI
    install.py        # multi-IDE wiring
    graph/            # reused graphify modules (extract/build/cluster/export/callflow)
    static/           # built dashboard (bundled)
  web/                # Vite + React dashboard (memory-graph + gitgraph reuse)
  skills/omni-memory/ # the skill package (SKILL.md + refs)
  NOTICE  LICENSE(MIT)  README.md  PLAN.md  pyproject.toml
```

---

## 9. Phases

- **P0 — core** — store + branch model + git metadata + Claude Code capture /
  inject / enforce hooks + `/omni-memory map` (graphify graph). Demoable:
  persistent, branch-aware, enforced memory in Claude Code.
- **P0.5 — minimal dashboard** — FastAPI + Graph & Memory-docs views (the demo UI).
- **P1 — branch-aware, full** — git provenance + **Repo Graph** view + Antigravity (MCP).
- **P2 — runtime flow capture** — endpoint→DB→Kafka→params/returns, success vs failure.
- **P3 — polish** — contradiction/forgetting tuning, Timeline/Flows views, more
  IDEs, perf, tests. **Launch:** Show HN / Trendshift, demo GIF.

---

## 10. Enhancements / roadmap (post-v1)

- **Contradiction & decay tuning** — confidence scoring, auto-expire stale memory.
- **Team mode** — optional shared memory (git-synced `.omni-memory/` or a light server).
- **PR/issue ingestion** — pull decisions from PR descriptions & review threads.
- **"Why did we…" answers** — trace a decision to its commit + chat origin.
- **Onboarding mode** — new dev asks the repo questions, gets guided tours.
- **Language coverage** — extend AST extractors (graphify pattern).
- **Runtime adapters** — OpenTelemetry / log-format plugins for flow capture.
- **Secrets safety** — redact params/returns; never store credentials.
- **Export** — GraphML / Obsidian vault of the whole memory.
- **Eval** — benchmark recall/enforcement (LongMemEval/LoCoMo style) for credibility.

---

## 11. Principles

- **Local-first, free.** No mandatory cloud, no paid data. Your code never leaves.
- **Deterministic where possible.** Code graph via AST (no LLM); LLM only for the
  semantic/conversational layer.
- **Never guess.** Enforcement makes the agent cite memory or admit it doesn't know.
- **Minimalist.** Clean monochrome UI, modular toggles, one-command install.
- **Attribution.** Credit graphify + supermemory (MIT) in NOTICE/README.
