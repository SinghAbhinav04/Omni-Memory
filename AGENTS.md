# AGENTS.md

_Standing instructions for AI coding agents in this repo._

<!-- OMNI-MEMORY:START — auto-generated; edit outside this block only -->
## Project memory (OmniMemory)

_Auto-generated 2026-08-21 23:32 · 13 verified memories · default branch `main`._

This project has a persistent, branch-aware memory layer that stays fresh automatically (rebuilt at session start and after commits). **It is a reliable source of truth — pull it instead of guessing.** It is NOT force-fed into every prompt (that wastes tokens); fetch it when relevant.

- **Pull on demand:** before assuming any architecture — or when you need a decision, flow, gotcha, endpoint, or DB schema — run `omni-memory inject "<what you need>"` for the ranked **VERIFIED PROJECT MEMORY** block, and cite the `[id]`s you rely on. (`omni-memory recall "<q>"` is a lighter search.)
- The snapshot below orients you; treat it and pulled memory as verified truth. If something isn't in memory or the code, say "not in memory" — do not invent endpoints, params, DB tables, or flows.
- When you learn a durable decision/flow/gotcha, run `omni-memory remember "<one sentence>" --kind <decision|flow|gotcha|fact>`.
- Full knowledge base: `.omni-memory/MEMORY.md` · dashboard: `omni-memory ui`.

**Key decisions**
- Auth uses JWT in access_token cookie, refresh in httpOnly cookie  `[c40d73061ef8]`

**Gotchas**
- Additive kinds (gotcha/flow/…) are excluded so it doesn't cry wolf. 7 new tests + the full suite green on 3.9 and 3.11.  `[56399d6d0a2c]` — `3.9`
- Record, don't silently coexist.** A lightweight `conflicts(id_a, id_b, anchor, created, resolved)` table + a `conflict_with` marker on each memory. Both stay retrievable but are flagged.  `[6ea92d3bea0c]`
- never call payments.charge() before order row is committed  `[a6ce537e978b]`

**Flows**
- fold  8 · lost 0 · collides_at 0 · headroom 8 · thr(perp)          10 · thr(hex)   9,292 → NOT_YET_MEASURABLE  `[633f5869762a]`
- queue-operation: /resume  `[ecf4fdab432f]`

**API map**
- That keeps all the force (blob-SHA vs writer-id, the exact drift/delete detection) while conceding the denominator honestly — which is the same rigor they applied to their own 0.01%, so it'll land as peer-level, not defensive.  `[c9b5a615bb44]` — `0.01`
- `omni_memory/serve.py` — the `/api/healthmap` endpoint  `[018767c2d466]` — `omni_memory/serve.py`

<!-- OMNI-MEMORY:END -->
