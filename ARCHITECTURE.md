# OmniMemory — Architecture

A persistent, branch-aware, git-anchored **memory & context layer for coding
agents**. It runs fully local, stores everything in a single SQLite file under
`.omni-memory/`, and injects *verified* project knowledge into any AI IDE so the
agent stops guessing at architecture.

This document is the map: what each module does, how data flows, and the design
rules that keep the pieces decoupled. Read it top-to-bottom once and the code
will navigate itself.

---

## Design rules (why the code looks the way it does)

1. **Zero-dependency core.** The engine is stdlib + SQLite. `tree-sitter` is the
   *only* dependency, and it's optional (a `python_version >= '3.10'` marker) —
   without it, Python still gets a real code graph via stdlib `ast`. This is why
   the bundled plugin and `pip install` both work with no compiler.
2. **Local-first & git-anchored.** No server, no cloud. Memory is scoped to the
   current git branch + its base, and anchored to the commit it was written at,
   so it can go *stale* when the code moves underneath it.
3. **Agent-driven extraction (no mandatory API key).** The thinking (turning a
   session into structured memories) is done by *the agent's own model*; the
   library just stores the result. An optional `llm.py` provider exists for
   headless capture.
4. **Self-updating.** A background watcher and IDE hooks keep the code graph,
   branches, staleness, and the exported context files current without the user
   running commands.
5. **Everything derives from the store.** The dashboard, `MEMORY.md`, and the
   cross-IDE `AGENTS.md` are all *renders* of the SQLite store — never a second
   source of truth.

---

## Layered module map

```
                 ┌─────────────────────── interfaces ───────────────────────┐
   cli.py        serve.py + static/index.html        install.py
   (commands)    (dashboard server + JSON API + UI)   (wire hooks + AGENTS.md)
                 └───────────────────────────┬──────────────────────────────┘
                                             │
     ┌──────────────── retrieval / export ───┴───────────────────┐
     inject.py        digest.py        agentsmd.py     context.py
     (prompt block)   (MEMORY.md)      (AGENTS.md)     (build snapshot)
                                             │
     ┌──────────────── memory lifecycle ─────┴───────────────────┐
     session_memory   cleanup      staleness     eviction     rank     savings
     (capture)        (noise)      (anchor)      (gc)         (rank)   (tokens saved)
                                             │
     ┌──────────────── provenance ───────────┴───────────────────┐
     witness          collector           identifier      systemmap
     (verify→use)     (liveness)          (id contract)   (architecture map)
                                             │
     ┌──────────────── code graph ───────────┴──── git provenance ┐
     graph/extract → graph/build            gitmeta → branch
     graph/affected · graph/proximity       (topology, classify, sync)
                                             │
                          ┌──────────────────┴──────────────────┐
                          store.py  (SQLite: memories, code graph,
                                     branches, commits, meta)
```

### Storage — the one source of truth
- **`store.py`** — the SQLite data layer. Tables: `memory`, `code_nodes` /
  `code_edges`, `branches`, `commits`, `flows`, `meta`. Owns the schema, its
  migrations (`_migrate`), and every read/write. Also drops a `*` `.gitignore`
  inside `.omni-memory/` so the live DB never shows up in a project's git.

### Git provenance
- **`gitmeta.py`** — a thin, read-only wrapper over the `git` CLI: current/default
  branch, `ahead_behind`, merge topology, per-branch commits, and
  `state_signature()` (a cheap fingerprint the watcher polls to decide when to
  rebuild).
- **`branch.py`** — branch-aware sync. `sync_git()` pulls git topology into the
  store; `classify_branches()` marks branches merged / abandoned / dormant so
  eviction can act; `full_refresh()` is the one-call orchestrator (code graph +
  topology + staleness + AGENTS.md) used by the watcher and the SessionStart hook.

### Code graph (what the "Code Graph" tab and symbol-level staleness run on)
- **`graph/extract.py`** — parse source into symbols (functions/methods/classes)
  with line ranges, signatures, docstrings, raised exceptions, and raw call
  names. Two interchangeable backends producing the *same* schema: tree-sitter
  (multi-language) and stdlib `ast` (Python fallback).
- **`graph/build.py`** — resolve call/base *names* to concrete symbol ids and
  persist the directed graph (`contains` / `calls` / `inherits`).
- **`graph/affected.py`** — reverse-BFS from changed symbols to their callers
  (impact analysis; feeds staleness).
- **`graph/proximity.py`** — weight symbols by hop-distance from the files in
  play (feeds the relevance ranker).

### Memory lifecycle
- **`session_memory.py`** — capture: turn a session (transcript + diff) into
  structured `{kind, text, files, symbols}` memories (agent- or model-driven).
- **`cleanup.py`** — noise filter: drop headings, aspirational prose, and doc
  fragments before they become memories.
- **`staleness.py`** — flag memories whose anchored code changed (symbol-level
  when a code graph exists, else file-level).
- **`eviction.py`** — garbage-collect dead/false memory: quarantine
  abandoned-branch and long-stale items (reversible), then human-gated purge.
- **`rank.py`** — relevance ranking: BM25F over memory fields + graph-proximity
  boost + citation feedback. Zero-dep, pure math.

### Provenance — the difference between a memory that resolves and one that's true
A locator can resolve perfectly *to the wrong observation*. These four modules keep the
separate questions separate, because collapsing them reads as confidence:

- **`witness.py`** — the VERIFY → USE window. Everything else verifies at *pull*;
  nothing carried that forward to the moment the agent *acted*. Pins each pulled
  memory's source digests at retrieval and re-checks them when its `[id]` is cited.
  Keeps `stale_at_use` (the world moved → revalidate) apart from `orphaned_at_use` (the
  source vanished → re-source), and a witness that bound nothing reports that the world
  was **not checked** — never "clean".
- **`collector.py`** — liveness for the read-ledger hook, checked against a witness the
  collector does not own (the runtime's own session transcript). A hook can die silently
  and leave an empty ledger indistinguishable from a quiet session while every stored
  record keeps reading `observed`. Three-valued: OK / FAIL / **SKIP** — an absent
  witness is never a pass.
- **`identifier.py`** — the id contract (`uuid4().hex[:12]`), re-measured against the
  store's own keys on every call rather than asserted in a doc. Reports the form
  invariant (foreign ids arrive via team shards) and a three-valued fold cost, because a
  zero on a small population is the *absence of a signal* and renders exactly like a
  clean bill of health.
- **`systemmap.py`** — the implemented architecture as an isometric city, projected from
  the store the way `healthmap.py` is. Every building carries a verdict re-resolved from
  the blob shas its memories were captured against, so the map can show which parts of
  *itself* have gone stale — the thing a generated architecture diagram cannot do.

### Retrieval & export (everything an agent actually reads)
- **`inject.py`** — build the `VERIFIED PROJECT MEMORY` block for a prompt:
  scope → rank → a tight, budget-capped set of memories with enforcement rules.
- **`context.py`** — assemble a compact repo snapshot to feed the model on `build`.
- **`digest.py`** — render the store into `.omni-memory/MEMORY.md` (human/agent KB).
- **`agentsmd.py`** — maintain the managed block in the repo-root `AGENTS.md` that
  *every* AI IDE reads on session start (the portable cross-IDE integration).

### AI / build
- **`llm.py`** — optional provider layer (Anthropic/OpenAI/Gemini via `urllib`,
  auto-detected from env) for headless capture when no agent is present.
- **`artifacts.py`** — generate the `api-map.md` / `linkup.md` doc artifacts.
- **`savings.py`** — the token-savings ledger: what a pull cost (`served`) against what
  re-reading the sources it cites would have cost (`baseline`). The baseline is
  deliberately conservative — only files a served memory cites *and that still resolve*
  count, capped per file, and a memory with no resolving anchor earns nothing — so the
  figure under-reports rather than flattering itself. `inject.build_block(event=...)`
  records it; callers that build a block only to MEASURE it pass no event, so the metric
  can never count its own reporting.

### Interfaces
- **`cli.py`** — the `omni-memory` command surface (status, doctor, config, inject,
  build, check, gc, flush, systemmap, ui, gain, share, snapshot, hook, …). One `cmd_*`
  per command. 0.10.0 cut 40 subcommands to 29 by folding duplicates into flags
  (`map`→`check --graph`, `artifact`→`build`, `install`→`bind`, `export`/`import`→
  `snapshot`, `inject-mode`/`branch-aware`→`config`); `hook`'s event names are a
  contract with `install._hooks_block()` and the published plugin's `hooks.json`.
- **`serve.py`** — the local dashboard: a stdlib `http.server` exposing a JSON
  API, a background **watcher** that rebuilds on git/file changes, and write
  endpoints (`/api/memory/*`, `/api/doc/save`, `/api/flush`).
- **`static/index.html`** — the single-file dashboard UI (Memory, Docs, Memory
  Graph, Code Graph, Repo Graph) with the node Inspector.
- **`install.py`** — wire hooks + `AGENTS.md` into an IDE (Claude Code hooks;
  Antigravity via AGENTS.md).

---

## Key data flows

**E · Verify → use (a memory that goes stale mid-task)**
```
inject.build_block()  → witness.pin()      # pin the sources this pull is trusted on
   … agent works, the code moves underneath it …
cli `used <id>` OR capture-time extract_citations()
   → witness.verify() → stale_at_use / orphaned_at_use reported SEPARATELY
   → witness.note_on_use() → store.add_event()   # the next session is told
```

**A · A prompt in an IDE (the hot path)**
```
UserPromptSubmit hook → cli `hook inject` → inject.build_block(store, root, query)
   → branch.scope() picks branch+base
   → proximity.context_from_files() weights nearby symbols
   → store.memories(...) → rank.rank() → budget-capped block → stdout → prompt
```

**B · Session start (any IDE, no re-reading the repo)**
```
SessionStart hook → cli `hook start` → branch.full_refresh(store, root)
   → graph.build_code_graph() + branch.sync_git() + staleness.recompute()
   → agentsmd.write()   # AGENTS.md refreshed from the persisted store
```

**C · Session end (capture)**
```
SessionEnd hook → cli `hook capture` → session_memory.capture_from_json()
   → cleanup.filter_items() → store.add_memory()
   → digest.write_digest() + agentsmd.write()  # exports kept in sync
```

**D · Dashboard (self-updating)**
```
serve.run_ui() → watcher thread polls gitmeta.state_signature()
   → on change: branch.full_refresh()   → publishes a new signature
   → browser polls /api/state → re-renders the open tab
edits: /api/memory/{add,update,delete} + /api/doc/save → store → _sync_docs()
```

---

## Where to start reading
- **The data model:** `store.py` (schema at the top).
- **How memory reaches the agent:** `inject.py` then `agentsmd.py`.
- **How the code graph is built:** `graph/extract.py` → `graph/build.py`.
- **How it stays fresh:** `branch.full_refresh` and `serve._watch`.
