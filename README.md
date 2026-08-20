<div align="center">

# ◇ OmniMemory

**The memory & context layer for coding agents.**

Your AI stops forgetting between chats — and stops confidently describing
architecture it never actually verified.

[![PyPI](https://img.shields.io/pypi/v/omni-memory-agent?color=b5791b&label=pypi)](https://pypi.org/project/omni-memory-agent/)
[![Python](https://img.shields.io/pypi/pyversions/omni-memory-agent?color=b5791b)](https://pypi.org/project/omni-memory-agent/)
![Zero dependencies](https://img.shields.io/badge/deps-stdlib%20%2B%20sqlite-b5791b)
![Local only](https://img.shields.io/badge/data-100%25%20local-4f8f60)

**Claude Code** · **OpenCode** · **Antigravity** · **Cursor** · **Windsurf**
<br>*(any AGENTS.md-aware IDE)*

</div>

---

## The problem

Every new session, your agent starts from zero. So it greps. It reads the same
four files it read yesterday, re-derives the same conclusions, and burns thousands
of tokens rediscovering what it already worked out — or worse, it *guesses*, and
invents an endpoint that has never existed.

Long-term memory helps, until it doesn't: memory written six weeks ago describes
code that has since moved, and nothing tells you which parts went stale. A confident
wrong answer is more expensive than no answer.

## The approach

OmniMemory anchors every memory to **git**. A memory records the blob SHA of the
bytes it was derived from, so it can be re-checked against your working tree at any
moment. When the code moves, the memory says so — `⚠STALE` in the block, a degraded
building on the map, an orphan in `doctor`.

```
CAPTURE (session + git) → STORE (SQLite, branch-tagged) → RANK + INJECT + ENFORCE
      → CHECK (staleness vs git) → VISUALIZE (dashboard · system map)
```

Local-first. No cloud, no telemetry, no paid data. The core is Python stdlib + SQLite.

---

## Install

```bash
pip install omni-memory-agent
```

<details>
<summary><b>Claude Code plugin</b> (session hooks + skill, no pip needed for injection)</summary>

```
/plugin marketplace add SinghAbhinav04/Omni-Memory
/plugin install omni-memory@singhabhinav
```
</details>

Runs with **no API key**. On Python ≥3.10 tree-sitter auto-installs for the exact
multi-language code graph; without it a stdlib backend still graphs Python (`ast`)
and JS/TS (regex) — approximate, but real.

## Quick start

Two commands people mix up, because they do **different** things:

| | |
|---|---|
| **`omni-memory build`** | **Creates the content.** Reads your repo, captures decisions/flows/gotchas, builds the code graph, writes the docs. |
| **`omni-memory bind`** | **Connects it to your IDE.** Installs session hooks + the cross-IDE `AGENTS.md`. Creates no memory. |

```bash
omni-memory build      # 1. build memory + docs from your repo   ← the content
omni-memory bind       # 2. wire it into your IDE (hooks + AGENTS.md)
omni-memory ui         # 3. browse memory, docs, graphs, savings
omni-memory doctor     # anytime: verify the setup is healthy
```

> **About `build`:** it reads your codebase with an agent/LLM. Run it **inside an AI
> IDE** (so the agent does the analysis) or set a model key (`omni-memory key gemini`).
> Without either, you still get the code graph + heuristic docs — just no AI-written facts.

After that it runs itself: memory is seeded once at session start, the agent pulls
what it needs on demand, and capture fires when the session ends or compacts.

---

## See it

**Ask memory instead of grepping.** The agent gets ground truth with citation IDs
and an instruction to admit ignorance rather than invent:

```console
$ omni-memory inject "how does rate limiting work"
=== VERIFIED PROJECT MEMORY (OmniMemory) ===
scope: 3 item(s) · branch 'main' + base 'main'
Rules: trust these; cite [id]s used; not here or in the code → say "not in memory"; re-verify ⚠STALE.

[a3f1c8d2] ✓ decision: Redis token-bucket for rate limiting, 100 req/min per key · gateway/limiter.py
[7b2e0194] gotcha: the limiter fails OPEN on Redis timeout — check before load tests · gateway/limiter.py
[c04d77ae] ~ flow ⚠STALE: request → limiter → router → handler · gateway/router.py
=== END MEMORY — cite [id]s you used ===
```

**Know what it's worth.** Every pull is measured against what re-reading the cited
sources would have cost:

```console
$ omni-memory gain
OmniMemory token savings  ·  project: my-service
  pulls            : 142  ·  37 session seed(s)  ·  since 2026-06-02
  served           :    48.2k tok    what memory actually cost
  baseline (re-read):  512.6k tok    reading the cited sources instead
  saved            :   464.4k tok    (90.6%)
  last 7 days      :    61.1k saved  ·  avg 3.2k / pull

  method: baseline = sum of the source files each served memory cites (capped 8k
          tok/file) + 250 tok search overhead per pull. Memories with no resolving
          anchor earn no credit. Estimate, ~4 chars/token.
```

**Find out when it's lying.** `doctor` reports measured coverage, not schema claims:

```console
$ omni-memory doctor
  ✓ git repository: yes
  ✓ code graph: 512 symbols (55 files)
  ✓ memories: 84 active, ⚠ 3 stale
  ⚠ source integrity: 71% of 63 bindable memories verified re-fetchable
       (45 fresh, 1 drifted, 6 orphaned, 11 unverifiable); 100% deletion-detectable
  ✓ observation binding: 88% observed (bound to bytes actually read), rest declared
  ✓ read collector: hook executes and records the read ledger
  ✗ collector ran (external witness): FAIL — agent touched 25 file(s) and the
       ledger is EMPTY → omni-memory bind
```

---

## What makes it different

Most memory tools store text and hope it stays true. The design goal here is that
**every claim can be falsified**, so here is what that buys, grouped by what it's for.

### Retrieval that doesn't waste your context

| | |
|---|---|
| **Pull-based** | Memory isn't force-fed into every prompt. It's seeded once at session start; after that the agent pulls on demand. Prefer enforcement? `config inject_mode auto`. |
| **Ranked, not dumped** | A BM25F ranker (symbol ≫ file > prose field weighting) surfaces the few memories matching your prompt, boosts those anchored **near what you're editing** via the call graph, and lifts what the agent has actually cited before. No embeddings, no key. |
| **Chain-complete** | A decision and the gotcha on the *same symbol* travel together. A ranked slice never hands you half a causal chain. |
| **Measured savings** | `gain` reports tokens saved vs. re-reading the cited sources — conservatively (see below). |

### Memory that tells you when it's stale

| | |
|---|---|
| **Symbol-level staleness** | `check` flags a memory ⚠ stale only when *its* symbol — or a symbol that calls it — actually changed in git. Not merely because the file was touched. |
| **Observation-bound provenance** | A read-time hook hashes files as the agent reads them, so a memory is anchored to the **bytes it was derived from**, not the file at session end. `UNBOUND_CAPTURE` flags a source that moved in between. |
| **Verify → use** | A memory pulled fresh can go stale *mid-task*. Pins taken at pull time are re-checked when the agent cites the ID, separating `stale_at_use` (revalidate) from `orphaned_at_use` (re-source) — different problems, different fixes. |
| **The collector is itself checked** | A hook that dies silently leaves an empty ledger that looks exactly like a quiet session, while every record keeps claiming `observed`. `doctor` checks it against the runtime's own transcript — a witness the collector doesn't own. |

### Memory that cleans up after itself

| | |
|---|---|
| **Noise filter** | Aspirational prose, doc boilerplate, and pasted chat/issue chatter never become memory. A durable fact *names* something concrete. |
| **Auto-quarantine** | Memories stranded on abandoned branches, or long-stale and uncited, are quarantined — reversibly. Memories the agent keeps citing are shielded. Hard deletion stays human-gated. |
| **Merge reconciliation** | When a branch merges, its memories reconcile onto the base: duplicates collapse, contradictions on the same symbol are flagged `⚠CONFLICT` in the block. `history <id>` shows the lineage. |
| **Protected memory** | `lock <id>` pins architecture/identity/standing rules as constitutional — never decays, never evicted. |

### Seeing the whole system

| | |
|---|---|
| **System Map** | `systemmap` renders the *implemented* architecture as an isometric city — buildings for runtime roles, routes for control/data/event paths, steppable flows, a `path:line-line` citation behind every claim. Unlike a generated diagram, **each building carries a verdict re-resolved from the blob SHA its memory was captured against**: drifted sources render degraded, deleted ones orphaned, un-remembered modules as visible blind spots. One self-contained HTML file, no network. |
| **Dashboard** | `ui` — token savings, browsable memory, the knowledge graph, the repo/branch graph, the system map. Auto-refreshes as you work. |
| **Team memory over git** | `share` writes a committed per-author shard. Each teammate owns a distinct file, so it merges with **zero conflicts**; a `git pull` syncs at session start, attributed and tagged `↗external`. Blob SHAs are content hashes, so a teammate's memory genuinely re-verifies on *your* clone. |

<details>
<summary><b>Why the savings number is deliberately pessimistic</b></summary>

A metric nobody can check is exactly the kind of confident claim this project exists
to avoid. So the baseline only counts what it can defend:

- only files a served memory **cites and that still resolve today** count;
- each distinct file counts **once per pull**, capped at 8k tokens — an agent skims
  a large file, it doesn't read a megabyte to find one fact;
- a memory with **no resolving anchor earns nothing**. We can't claim to have saved
  a read that could never have happened;
- a flat 250 tok/pull stands in for the grep round-trip that would have come first.

The figure therefore under-reports, and rises only as capture quality genuinely
improves. The method prints next to the number, every time.
</details>

---

## Commands

```bash
# setup
omni-memory build            # build memory + DOCS from the repo (MEMORY.md, api-map, linkup)
omni-memory bind [ide]       # wire an IDE: session hooks + AGENTS.md (auto-detects)
omni-memory ui               # dashboard: savings · memory · docs · code & repo graph
omni-memory doctor           # diagnose setup (git, store, graph, hooks, AGENTS.md, AI)
omni-memory status | on | off
omni-memory config [key] [value]             # every tunable in one place (no args = list)

# using memory
omni-memory recall <q>                       # search memory instead of grepping
omni-memory inject "<q>"                     # the VERIFIED PROJECT MEMORY block for a task
omni-memory remember "…" [--kind …] [--global]   # add one by hand (--global = every project)
omni-memory forget <id> · used <id>… · lock <id> [--off]
omni-memory branches                         # git topology + per-branch memory

# keeping it fresh
omni-memory check [--graph]  # re-anchor vs git; flag ⚠ stale (--graph rebuilds the graphs)
omni-memory systemmap        # one self-contained HTML: the architecture as an isometric
                             # city, every claim citation-backed and verdict-carrying
omni-memory gc [--dry-run] [--purge] [--restore <id|branch>]   # quarantine dead/false memory
omni-memory gain [--history] # tokens SAVED vs re-reading the sources, + footprint + tuning
omni-memory capture [--prompt build|session] # ingest extraction JSON / print the prompt

# merges / conflicts
omni-memory conflicts        # memories that contradict each other after a branch merge
omni-memory resolve <id> [--keep|--both]     # settle a conflict
omni-memory history <id>     # supersession lineage: what a memory replaced, and what replaced it

# team / sharing / reset
omni-memory share [--status] # write your committed per-author shard · --status = who contributed
omni-memory sync             # pull teammates' shared memory (also automatic at session start)
omni-memory snapshot [--out|--in FILE] [--global]   # portable JSON memory, both directions
omni-memory flush [--scope all|memory|graph] [-y]   # wipe to rebuild from scratch
omni-memory key <gemini|anthropic|openai>    # store a model key for the AI build pass
```

<details>
<summary><b>Tuning</b> — <code>omni-memory config</code></summary>

| Setting | Default | What it does |
|---|---|---|
| `inject_mode` | `session` | `session` seeds once then pulls (cheapest) · `auto` injects every prompt · `manual` pull-only |
| `branch_aware` | `on` | Scope memory to the current branch + its base |
| `inject_max_items` | `8` | Max memories in one block |
| `inject_char_budget` | `1400` | Max characters of the block |
| `inject_global_items` | `3` | Cross-project memories from `~/.omni-memory` |
| `chain_complete` | `on` | Pull a decision's co-anchored gotchas with it |
| `auto_import_shared` | `on` | Auto-load a committed `omni-memory.json` on a fresh clone |
</details>

---

## Status

**Shipped:** store · git provenance · branch-aware scoping · capture/inject/enforce ·
BM25F ranker with code-graph proximity and citation feedback · symbol-level staleness ·
noise filter · memory hygiene (auto-quarantine, human-gated purge) · tree-sitter code
graph (Python/JS/TS, stdlib `ast` fallback) · observation-bound provenance ·
verify→use window · merge reconciliation · team sharing · system map · dashboard ·
token-savings ledger.

**Next:** Antigravity via MCP, more languages, runtime request-flow capture.
See [`PLAN.md`](PLAN.md).

## License

**Proprietary © 2026 Abhinav Singh. All rights reserved.**
OmniMemory is *not* open source — you may install and run the official published
package, but not copy, modify, redistribute, or claim it as your own.
See [`LICENSE`](LICENSE).
