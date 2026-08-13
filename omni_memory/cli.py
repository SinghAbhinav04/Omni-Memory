"""`omni-memory` / `/omni-memory` command dispatch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__, branch as branchmod, digest, gitmeta, graphbuild, inject
from . import session_memory as sm
from .store import Store, find_project_root


def _store():
    """Open the store for the project the CWD lives in. Every command starts here."""
    root = find_project_root()
    return Store(root), root


def cmd_status(args):
    """`status` — layer state, current branch, memory counts, AI provider, store path."""
    s, root = _store()
    _bootstrap_shared(s, root)                 # fresh clone → load committed memory
    branchmod.sync_git(s, root)
    c = s.counts()
    on = s.get_meta("enabled", True)
    ba = s.get_meta("branch_aware", True)
    cur = gitmeta.current_branch(root)
    from . import llm
    ai = llm.provider() or "none (set GEMINI_API_KEY)"
    mode = s.get_meta("inject_mode", "session")
    print(f"OmniMemory {__version__}  ·  project: {root.name}")
    print(f"  layer: {'ON' if on else 'OFF'}   branch-aware: {'ON' if ba else 'OFF'}   "
          f"inject: {mode}   AI: {ai}")
    stale = s.db.execute(
        "SELECT COUNT(*) n FROM memory WHERE status='active' AND stale=1").fetchone()["n"]
    stale_note = f"   ⚠ {stale} stale (run: omni-memory check)" if stale else ""
    print(f"  branch: {cur}   memories: {c.get('active',0)} active, "
          f"{c.get('merged',0)} merged, {c.get('superseded',0)} superseded")
    print(f"  store: {s.dir}{stale_note}")
    return 0


def cmd_toggle(args):
    """`on` / `off` — enable or disable the whole memory layer (injection + capture)."""
    s, _ = _store()
    s.set_meta("enabled", args.cmd == "on")
    print(f"OmniMemory {'enabled' if args.cmd == 'on' else 'disabled'}.")
    return 0


def cmd_branch_aware(args):
    """`branch-aware` — toggle scoping memory to the current branch+base vs. all branches."""
    s, _ = _store()
    new = not s.get_meta("branch_aware", True)
    s.set_meta("branch_aware", new)
    print(f"branch-aware: {'ON' if new else 'OFF'}")
    return 0


def cmd_inject_mode(args):
    """`inject-mode [auto|session|manual]` — how memory reaches the agent.

      session  seed the ranked block ONCE at session start, then the agent pulls
               on demand with `omni-memory inject "<q>"` (default — saves tokens,
               memory stays fresh via the SessionStart refresh).
      auto     inject relevant memory into every prompt (enforced, higher token cost).
      manual   never auto-inject, not even at session start — pull only.
    """
    s, _ = _store()
    mode = getattr(args, "mode", None)
    if mode:
        if mode not in ("auto", "session", "manual"):
            print("mode must be one of: auto | session | manual")
            return 1
        s.set_meta("inject_mode", mode)
    print(f"inject-mode: {s.get_meta('inject_mode', 'session')}")
    return 0


def cmd_remember(args):
    """`remember <text> [--kind] [--global] [--verified|--inferred]` — add one
    memory by hand. Evidence marks how it was learned: --verified (confirmed by
    outcome, trusted + pruned last), --inferred (guessed, pruned first), else
    stated. --global stores it in ~/.omni-memory (injects into every project)."""
    evidence = ("verified" if getattr(args, "verified", False)
                else "inferred" if getattr(args, "inferred", False) else "stated")
    if getattr(args, "glob", False):
        from .store import Store, Memory, global_dir
        gs = Store(exact_dir=global_dir())
        m = gs.add_memory(Memory(text=" ".join(args.text), kind=args.kind,
                                 branch="global", source="manual", evidence=evidence))
        print(f"[+] remembered GLOBALLY [{m.id}] ({m.kind} · {evidence}): {m.text}")
        return 0
    s, root = _store()
    m = sm.remember(s, root, " ".join(args.text), kind=args.kind, source="manual",
                    evidence=evidence)
    if getattr(args, "lock", False):
        s.set_protected(m.id, True)             # constitutional — never decays/evicts
    digest.write_digest(s)
    lock = " · 🔒 protected" if getattr(args, "lock", False) else ""
    print(f"[+] remembered [{m.id}] ({m.kind} · {evidence}{lock}, branch {m.branch}): {m.text}")
    return 0


def cmd_lock(args):
    """`lock <id>` / `unlock <id>` — pin a memory as constitutional (never decays
    or gets evicted) or release it. For architecture, identity, and standing rules."""
    s, _ = _store()
    on = args.cmd == "lock"
    ok = s.set_protected(args.id, on)
    print(f"[+] {'🔒 locked' if on else 'unlocked'} [{args.id}]" if ok
          else f"no memory with id {args.id}")
    return 0 if ok else 1


def cmd_capture(args):
    """Ingest the agent's extraction JSON (stdin) → memories."""
    s, root = _store()
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    raw = sys.stdin.read()
    n, dropped = sm.capture_from_json(s, root, raw, source="session")
    digest.write_digest(s)
    note = f"; {dropped} dropped as noise" if dropped else ""
    print(f"[+] captured {n} mem{note}  (branch {gitmeta.current_branch(root)}); digest updated")
    return 0


def cmd_digest(args):
    """`digest` — (re)render the store into `.omni-memory/MEMORY.md`."""
    s, _ = _store()
    out = digest.write_digest(s)
    print(f"[+] knowledge base → {out}")
    return 0


def cmd_build(args):
    """One-time bootstrap: AI-written facts from the repo + docs + graph."""
    from . import context, llm
    s, root = _store()
    print(f"[*] building OmniMemory for {root.name} …")
    branchmod.sync_git(s, root)
    # the CODE graph (functions/calls) — was missing here, so a first `build`
    # left the Code Graph tab empty until the watcher/`map` ran.
    try:
        from .graph import build as codegraph
        cg = codegraph.build_code_graph(s, root)
        print(f"[+] code graph: {cg['nodes']} symbols, {cg['edges']} edges "
              f"({cg['backend']}, {cg['files_parsed']} files)")
    except Exception as e:  # noqa: BLE001
        print(f"[!] code graph skipped ({e})")
    g = graphbuild.build_graph(s, root)
    (s.dir / "graph.json").write_text(json.dumps(g, indent=2), encoding="utf-8")

    ai_added = 0
    use_ai = llm.available() and not args.no_ai
    if use_ai:
        print(f"[*] AI pass via {llm.provider()} — reading the codebase …")
        try:
            ctx = context.gather(root)
            items = llm.extract_memories(sm.BUILD_PROMPT, ctx)
            ai_added, _dropped = sm.remember_many(s, root, items, source="ai-build")
            print(f"[+] AI wrote {ai_added} facts from the codebase")
            from . import artifacts
            for p in artifacts.generate_all(s, root):
                print(f"[+] artifact → {p}")
        except Exception as e:  # noqa: BLE001
            print(f"[!] AI pass failed ({e}); falling back to docs.")
            use_ai = False

    doc_added, scanned = (0, 0)
    if not args.no_docs:
        doc_added, scanned = sm.ingest_docs(s, root)
    digest.write_digest(s)

    print(f"[+] graph: {len(g['nodes'])} nodes  ·  AI facts: {ai_added}  ·  "
          f"docs: {doc_added} from {scanned} file(s)")
    print(f"[+] knowledge base → {s.dir / 'MEMORY.md'}")
    if not use_ai and not args.no_ai:
        print("\n[i] No model key set → only heuristic/doc facts. For real AI facts:")
        print("    export GEMINI_API_KEY=...   (then re-run: omni-memory build)")
    return 0


def cmd_prompt(args):
    """`prompt build|session` — print the extraction instructions for the agent to
    follow, whose JSON output is then piped back into `capture`."""
    print(sm.BUILD_PROMPT if args.which == "build" else sm.EXTRACTION_PROMPT)
    return 0


def cmd_artifact(args):
    """Generate the AI-written docs (api-map / linkup)."""
    from . import artifacts, llm
    if not llm.available():
        print("[i] needs a model key. export GEMINI_API_KEY=... then retry.")
        return 1
    s, root = _store()
    which = args.which
    print(f"[*] generating {which} via {llm.provider()} …")
    try:
        if which in ("apimap", "all"):
            print(f"[+] {artifacts.generate_apimap(s, root)}")
        if which in ("linkup", "all"):
            print(f"[+] {artifacts.generate_linkup(s, root)}")
    except Exception as e:  # noqa: BLE001
        print(f"[!] failed: {e}")
        return 1
    return 0


def cmd_inject(args):
    """`inject <query>` — print the VERIFIED PROJECT MEMORY block for a request
    (what the agent should treat as ground truth at the start of a task)."""
    s, root = _store()
    if not s.get_meta("enabled", True):
        return 0
    branchmod.refresh_if_stale(s, root)         # a pull always reflects the current code
    block = inject.build_block(s, root, query=" ".join(args.query or []),
                               files=args.file or None)
    if block:
        print(block)
    return 0


def cmd_recall(args):
    """`recall <query>` — search memory and print matches (query instead of grep)."""
    s, root = _store()
    cur, base = branchmod.scope(s, root)
    mems = s.memories(branch=None if cur == "*" else cur, base=base,
                      query=" ".join(args.query), limit=30)
    if not mems:
        print("not in memory.")
        return 0
    for m in mems:
        where = ("  · " + ", ".join(m["files"][:3])) if m["files"] else ""
        stale = " ⚠STALE" if m.get("stale") else ""
        print(f"[{m['id']}] {m['kind']} ({m['branch']}){stale}: {m['text']}{where}")
    return 0


def cmd_branches(args):
    s, root = _store()
    branchmod.sync_git(s, root)
    for b in s.branches():
        star = "*" if b["name"] == gitmeta.current_branch(root) else " "
        merged = f" → merged into {b['into_branch']}" if b["status"] == "merged" else ""
        who = f"  by {b['creator']}" if b.get("creator") else ""
        print(f"{star} {b['name']:24} {b['status']}{merged}{who}")
    return 0


def cmd_check(args):
    """Re-anchor memories against git: flag any whose code changed since they
    were written as ⚠ stale (they keep their content — re-verify, don't trust).
    Rebuilds the code graph first so staleness is symbol/caller-precise."""
    from . import staleness
    from .graph import build as codegraph
    s, root = _store()
    try:
        codegraph.build_code_graph(s, root)
    except Exception:  # noqa: BLE001
        pass  # fall back to file-level staleness
    r = staleness.recompute(s, root)
    print(f"[+] checked {r['checked']} anchored memory · {r['stale']} stale · "
          f"{r['cleared']} cleared  ({r['level']}-level)")
    from . import eviction
    sw = eviction.sweep(s, root, dry_run=False, purge=False)
    if sw["quarantined"]:
        print(f"[+] quarantined {len(sw['quarantined'])} dead/false memory "
              f"(reversible: omni-memory restore <id|branch>)")
        for c in sw["quarantined"][:8]:
            print(f"    ⊘ [{c['id']}] {c['reason']}: {c['text']}")
    if sw["purgeable"]:
        print(f"[i] {len(sw['purgeable'])} quarantined >grace & uncited — "
              f"remove with: omni-memory gc --purge")
    if r["stale"]:
        rows = s.db.execute(
            "SELECT id, kind, text FROM memory "
            "WHERE status='active' AND stale=1 ORDER BY stale_since DESC LIMIT 20"
        ).fetchall()
        for row in rows:
            print(f"    ⚠ [{row['id']}] {row['kind']}: {row['text'][:80]}")
    return 0


def cmd_forget(args):
    s, _ = _store()
    ok = s.forget(args.id)
    print("forgotten." if ok else "no such memory.")
    return 0


def cmd_map(args):
    s, root = _store()
    branchmod.sync_git(s, root)
    g = graphbuild.build_graph(s, root)
    out = s.dir / "graph.json"
    out.write_text(json.dumps(g, indent=2), encoding="utf-8")
    print(f"[+] memory graph: {len(g['nodes'])} nodes, {len(g['edges'])} edges → {out}")

    from .graph import build as codegraph, extract
    cg = codegraph.build_code_graph(s, root)
    if cg["files_parsed"]:
        print(f"[+] code graph ({cg['backend']}): {cg['nodes']} symbols, "
              f"{cg['edges']} edges over {cg['files_parsed']} file(s)")
        print(f"    {cg['kinds']}  ·  {cg['rels']}")
    if not extract.available():
        print("    [i] deep multi-language graph needs tree-sitter → "
              "pip install omni-memory-agent (Python is graphed via stdlib ast).")
    return 0


def _read_transcript(path, start_line: int = 0):
    """Claude Code transcript is JSONL; flatten to 'role: text' for extraction.
    Returns (text, total_lines). `start_line` skips lines already captured on an
    earlier run (e.g. a PreCompact capture before this SessionEnd), so the same
    exchanges aren't re-extracted every time."""
    from pathlib import Path
    if not path or not Path(path).exists():
        return "", start_line
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    total = len(lines)
    out = []
    for line in lines[max(start_line, 0):]:
        try:
            obj = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        msg = obj.get("message") or obj
        role = msg.get("role") or obj.get("type", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content
                               if isinstance(b, dict) and b.get("type") == "text")
        if isinstance(content, str) and content.strip():
            out.append(f"{role}: {content.strip()}")
    return "\n".join(out)[-140_000:], total


def cmd_flush(args):
    """Wipe stored memory/graph so it can be rebuilt from scratch."""
    s, root = _store()
    scope = args.scope
    what = {"all": "ALL memory + code graph + git topology",
            "memory": "memories only", "graph": "code graph only"}[scope]
    if not args.yes:
        ans = input(f"Flush {what} for this project? This cannot be undone. [y/N] ")
        if ans.strip().lower() not in ("y", "yes"):
            print("Aborted.")
            return 1
    counts = s.flush(scope)
    total = sum(counts.values())
    print(f"[+] flushed {total} rows: " +
          ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    # keep the derived artifacts consistent with the now-empty store
    try:
        digest.write_digest(s)
        from . import agentsmd
        agentsmd.write(s, root)
    except Exception:  # noqa: BLE001
        pass
    hint = {"all": "omni-memory build   (or map + check)",
            "memory": "omni-memory build",
            "graph": "omni-memory map"}[scope]
    print(f"    rebuild with: {hint}")
    return 0


def _open_store(use_global):
    """Project store by default, or the shared ~/.omni-memory store with --global."""
    if use_global:
        from .store import Store, global_dir
        d = global_dir()
        return Store(exact_dir=d), d
    return _store()


def cmd_share(args):
    """`share` — export your memories to a committed per-author shard under
    `.omni-memory/team/`, so teammates get them on their next session. Each author
    owns a distinct file, so it merges without conflicts."""
    from . import team
    s, root = _store()
    path = team.write_shard(s, root)
    if not path:
        print("no git identity — set `git config user.email` first, then re-run.")
        return 1
    rel = path.relative_to(root)
    print(f"[+] wrote your team shard → {rel}")
    if gitmeta._git(root, "check-ignore", str(rel)):   # a root .gitignore hides it
        print(f"    ⚠ {rel} is git-ignored — remove `.omni-memory/` from your repo's root")
        print("      .gitignore so the shard can be committed (the store self-ignores the db).")
    print(f"    commit it:  git add {rel} && git commit -m 'omni-memory: share'")
    print("    teammates get it automatically on their next session (or `omni-memory sync`).")
    return 0


def cmd_sync(args):
    """`sync` — import teammates' shared memory shards (run after a `git pull`;
    also happens automatically at session start)."""
    from . import team
    s, root = _store()
    n = team.sync(s, root)
    if n:
        digest.write_digest(s)
        print(f"[+] synced {n} shared memories from teammates (↗external — re-verify locally)")
    else:
        print("up to date — no new shared memories."
              + ("" if team.enabled(root) else " (team sharing not set up: run `omni-memory share`)"))
    return 0


def cmd_team(args):
    """`team` — who has contributed to this project's memory, and shard status."""
    from . import team
    s, root = _store()
    st = team.status(s, root)
    print(f"you: {st['me'] or '(no git identity — set git config user.email)'}")
    print("memory by author:")
    for r in st["by_author"]:
        print(f"  {r['c']:>4}  {r['a']}")
    if st["shards"]:
        print("committed shards: " + ", ".join(st["shards"]))
    if not st["enabled"]:
        print("team sharing not set up — `omni-memory share` writes your shard, commit it,")
        print("teammates `git pull` then sync automatically at session start.")
    return 0


def cmd_export(args):
    """`export [file] [--global]` — write a portable JSON snapshot of memories.
    Default target is `omni-memory.json` at the repo root; commit it to share the
    memory with teammates / other clones, or move it to another machine/IDE."""
    s, root = _open_store(args.glob)
    data = s.export_memories()
    out = Path(args.file) if args.file else (find_project_root() / "omni-memory.json")
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[+] exported {len(data['memories'])} memories → {out}")
    if not args.file:
        print("    tip: commit omni-memory.json to share memory across clones/IDEs.")
    return 0


def cmd_import(args):
    """`import [file] [--global]` — load memories from a JSON export into this
    store (existing ids skipped, so it's safe to re-run and to merge exports)."""
    s, root = _open_store(args.glob)
    src = Path(args.file) if args.file else (find_project_root() / "omni-memory.json")
    if not src.exists():
        print(f"no export file at {src}  (run `omni-memory export` first, or pass a path).")
        return 1
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[!] couldn't read export: {e}")
        return 1
    n = s.import_memories(data)
    digest.write_digest(s)
    try:
        from . import agentsmd
        agentsmd.write(s, root)
    except Exception:  # noqa: BLE001
        pass
    print(f"[+] imported {n} new memories from {src}")
    return 0


def cmd_usage(args):
    """`usage [--max-items N] [--budget CHARS]` — show the approximate token
    footprint of everything OmniMemory injects, and tune the per-prompt cost."""
    s, root = _store()
    if args.max_items is not None:
        s.set_meta("inject_max_items", args.max_items)
    if args.budget is not None:
        s.set_meta("inject_char_budget", args.budget)
    if args.max_items is not None or args.budget is not None:
        print("[+] updated injection limits.\n")

    def toks(txt):
        return (len(txt or "") + 3) // 4        # rough ~4 chars/token

    block = inject.build_block(s, root, query="")
    ag = root / "AGENTS.md"
    ag_block = ""
    if ag.exists():
        from . import agentsmd
        t = ag.read_text(encoding="utf-8", errors="ignore")
        ag_block = (t.split(agentsmd.START, 1)[1].split(agentsmd.END, 1)[0]
                    if agentsmd.START in t and agentsmd.END in t else t)
    mem_md = s.dir / "MEMORY.md"
    mi = int(s.get_meta("inject_max_items", inject._MAX_ITEMS))
    cb = int(s.get_meta("inject_char_budget", inject._CHAR_BUDGET))
    print("OmniMemory token footprint  (approx · ~4 chars/token)")
    print(f"  per-prompt injection : ~{toks(block):>4} tok   · rides EVERY message")
    print(f"  session-start seed   : ~{toks(block):>4} tok   · once per session")
    print(f"  AGENTS.md standing   : ~{toks(ag_block):>4} tok   · loaded by Antigravity/Cursor each session")
    if mem_md.exists():
        print(f"  MEMORY.md (@-ref)    : ~{toks(mem_md.read_text(encoding='utf-8', errors='ignore')):>4} tok   · only if you @-reference it")
    print(f"\n  settings: max_items={mi}  char_budget={cb}")
    print("  per-prompt block re-injects only when memory changes (deduped) —"
          " not every turn.")
    from . import llm
    cap = "LLM" if llm.autocapture_ok() else "heuristic (free — no claude -p on autopilot)"
    print(f"  SessionEnd auto-capture: {cap}."
          + ("" if llm.autocapture_ok() else " Set OMNI_HEADLESS_LLM=1 or an API key for LLM capture."))
    print("  lower it: omni-memory usage --max-items 6 --budget 1000")
    return 0


def cmd_hook(args):
    """Claude Code hook entrypoint. MUST NEVER raise — a hook that errors would
    disrupt the user's prompt/session — so the whole body is guarded and any
    failure degrades to a silent no-op."""
    try:
        return _run_hook(args)
    except Exception:  # noqa: BLE001 — a broken hook must not break the session
        return 0


_SHARED_MAX_BYTES = 2_000_000


_INJECT_REFRESH_EVERY = 20   # re-emit an unchanged block every N prompts (compaction safety)


def _write_inject_state(path, h, count):
    try:
        path.write_text(f"{h} {count}", encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _mark_injected(s, block):
    import hashlib
    _write_inject_state(s.dir / ".inject_cache",
                        hashlib.sha1(block.encode("utf-8")).hexdigest(), 0)


def _should_inject(s, block):
    """True only when this block differs from the last one injected (the memory
    changed), or enough prompts have passed that it may have scrolled out of
    context. Otherwise the block is already in the session — re-emitting it just
    burns tokens every turn."""
    import hashlib
    h = hashlib.sha1(block.encode("utf-8")).hexdigest()
    p = s.dir / ".inject_cache"
    last_h, count = "", 0
    try:
        parts = p.read_text(encoding="utf-8").split()
        last_h, count = parts[0], (int(parts[1]) if len(parts) > 1 else 0)
    except Exception:  # noqa: BLE001
        pass
    if h != last_h or count + 1 >= _INJECT_REFRESH_EVERY:
        _write_inject_state(p, h, 0)
        return True
    _write_inject_state(p, h, count + 1)
    return False


def _bootstrap_shared(s, root):
    """Fresh clone with committed memory: auto-load `omni-memory.json` the first
    time (store still empty) so a teammate/other machine starts already-remembered
    without running `import`. Idempotent — skips once the store has memories.

    Security: the file is repo-controlled, so imported memories are tagged
    source="shared" (rendered ↗external, NOT trusted as this project's own truth),
    size-capped, and the whole behavior is gated by the `auto_import_shared` meta
    flag (set it false to require an explicit `omni-memory import`)."""
    try:
        if not s.get_meta("auto_import_shared", True):
            return
        if s.counts().get("active", 0) > 0:
            return
        shared = root / "omni-memory.json"
        if not shared.exists() or shared.stat().st_size > _SHARED_MAX_BYTES:
            return
        n = s.import_memories(json.loads(shared.read_text(encoding="utf-8")), source="shared")
        if n:
            digest.write_digest(s)
            print(f"omni-memory: loaded {n} shared memories from omni-memory.json "
                  "(marked ↗external — verify before trusting). Disable: set "
                  "auto_import_shared=false.", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _run_hook(args):
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        data = {}
    s, root = _store()
    if not s.get_meta("enabled", True):
        return 0
    mode = s.get_meta("inject_mode", "session")   # session (default) | auto | manual
    if args.event == "start":                  # SessionStart → refresh + ensure AGENTS.md
        _bootstrap_shared(s, root)             # fresh clone → load committed memory (legacy single file)
        try:                                    # team sync: pull teammates' shards every session
            from . import team
            tn = team.sync(s, root)
            if tn:
                print(f"omni-memory: synced {tn} shared memories from teammates "
                      "(↗external — re-verify locally).", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass
        # Rebuild ONLY if git state changed since last time (the code graph is
        # persisted in SQLite) — so opening a session doesn't re-parse the whole
        # repo when nothing moved. Keeping the store fresh here is what lets the
        # agent PULL memory later and trust it as current.
        branchmod.refresh_if_stale(s, root)
        from . import agentsmd
        agentsmd.write(s, root)                 # durable pull instructions + snapshot
        if mode != "manual":                    # seed the ranked block ONCE per session
            evs = s.recent_events(3)            # cross-session event bus: what changed lately
            if evs:
                print("=== RECENT ACTIVITY (prior sessions) ===")
                for e in evs:
                    print(f"· {e['summary']} — branch {e['branch']}")
            block = inject.build_block(s, root, query="")
            if block:
                print(block)                    # grounds a cold agent; then it pulls on demand
                _mark_injected(s, block)
        return 0
    if args.event == "inject":                 # UserPromptSubmit
        if mode != "auto":                      # pull mode (default): the agent fetches
            return 0                            # memory itself via `omni-memory inject` — no
                                                # per-prompt token cost, memory kept fresh above
        block = inject.build_block(s, root, query=data.get("prompt", ""))
        # auto mode only: de-dupe so an identical block isn't re-emitted every prompt.
        if block and _should_inject(s, block):
            print(block)                        # stdout is added to the prompt context
        return 0
    if args.event in ("capture", "precompact"):  # SessionEnd OR PreCompact → extract + store
        # PreCompact fires BEFORE the context is summarized away, so unfiled memory
        # isn't lost if the session compacts (or dies) before it ends. Capture reads
        # the transcript heuristically — it does NOT ask the model to summarize at
        # full context — so the pre-compaction pass is safe. An offset per transcript
        # avoids re-extracting the same exchanges at the later SessionEnd.
        tpath = data.get("transcript_path") or ""
        offsets = s.get_meta("capture_offsets", {}) or {}
        start = offsets.get(tpath, 0) if tpath else 0
        text, total = _read_transcript(tpath, start_line=start)
        if text:
            n, dropped = sm.capture_from_json(s, root, text, source="session")
            # citation feedback: lift memories the agent actually cited [id]
            ids = {r["id"] for r in s.db.execute(
                "SELECT id FROM memory WHERE status='active'")}
            bumped = s.bump_uses(sm.extract_citations(text, ids))
            digest.write_digest(s)
            try:
                from . import agentsmd
                agentsmd.write(s, root)
            except Exception:  # noqa: BLE001
                pass
            note = f", {dropped} dropped as noise" if dropped else ""
            print(f"omni-memory: captured {n} memories{note}, +{bumped} citations "
                  f"({args.event})", file=sys.stderr)
            if n:                               # cross-session event bus entry
                s.add_event(gitmeta.current_branch(root),
                            f"captured {n} memories" + (f" (+{bumped} cited)" if bumped else ""))
            try:                                # keep my committed team shard current
                from . import team
                if team.enabled(root):
                    team.write_shard(s, root)
            except Exception:  # noqa: BLE001
                pass
        if tpath:                               # advance the offset even if nothing new
            offsets[tpath] = total
            s.set_meta("capture_offsets", offsets)
        return 0
    return 0


def cmd_used(args):
    """Record that memories were used/cited (feeds the relevance ranker)."""
    s, _ = _store()
    n = s.bump_uses(args.id)
    print(f"[+] recorded use of {n} memory")
    return 0


def cmd_gc(args):
    """Garbage-collect dead/false memory: quarantine abandoned-branch and stale
    memories (reversible); --purge hard-deletes long-quarantined uncited ones."""
    from . import eviction
    s, root = _store()
    sw = eviction.sweep(s, root, dry_run=args.dry_run, purge=args.purge)
    verb = "would quarantine" if args.dry_run else "quarantined"
    noise = sw.get("noise", [])
    print(f"[+] {verb} {len(sw['quarantined'])} memory"
          + (f" + {len(noise)} conversational/thread noise" if noise else ""))
    for c in sw["quarantined"][:30]:
        print(f"    ⊘ [{c['id']}] {c['branch']} · {c['reason']} "
              f"(score {c['score']}): {c['text']}")
    for c in noise[:30]:
        print(f"    ⊘ [{c['id']}] {c['branch']} · noise: {c['text']}")
    if args.purge:
        print(f"[+] purged {len(sw['purged'])} long-quarantined uncited memory")
    elif sw["purgeable"]:
        print(f"[i] {len(sw['purgeable'])} eligible for purge "
              f"(quarantined >grace, never cited) — add --purge to delete")
    return 0


def cmd_restore(args):
    """Un-quarantine a memory by id, or every memory from a branch."""
    s, _ = _store()
    n = s.restore(args.target) or s.restore_branch(args.target)
    print(f"[+] restored {n} memory" if n else "nothing to restore for that id/branch.")
    return 0


def cmd_key(args):
    """Store an API key securely (~/.omni-memory/credentials.json, chmod 600)."""
    import getpass
    import json as _json
    import os as _os
    from . import llm
    env = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
           "openai": "OPENAI_API_KEY"}[args.provider]
    val = getpass.getpass(f"Paste {args.provider} API key (hidden): ").strip()
    if not val:
        print("nothing entered.")
        return 1
    llm.CREDS.parent.mkdir(parents=True, exist_ok=True)
    creds = {}
    if llm.CREDS.exists():
        try:
            creds = _json.loads(llm.CREDS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            creds = {}
    creds[env] = val
    llm.CREDS.write_text(_json.dumps(creds, indent=2), encoding="utf-8")
    _os.chmod(llm.CREDS, 0o600)
    print(f"[+] saved {args.provider} key → {llm.CREDS} (chmod 600, gitignored)")
    return 0


def cmd_ui(args):
    from . import serve
    return serve.run_ui(port=args.port)


def cmd_install(args):
    """`install [--platform]` — wire hooks + AGENTS.md into a specific IDE."""
    from . import install
    return install.install(platform=args.platform)


def cmd_bind(args):
    """`bind [ide]` — one-command onboarding; auto-detects the IDE if not given
    and sets up hooks (where supported) + the cross-IDE AGENTS.md."""
    from . import install
    return install.bind(ide=args.ide or "auto")


def _plugin_hooks_present() -> bool:
    """Are OmniMemory's hooks wired via the installed Claude Code plugin? The
    plugin ships its own hooks.json (SessionStart/UserPromptSubmit/SessionEnd),
    which lives under ~/.claude/plugins/ — the project's .claude/settings.json is
    never touched, so a project-only grep misses it (the 'hooks not installed'
    false alarm)."""
    base = Path.home() / ".claude" / "plugins"
    if not base.exists():
        return False
    try:
        for p in base.rglob("hooks.json"):
            if "omni_memory hook" in p.read_text(encoding="utf-8", errors="ignore"):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def cmd_doctor(args):
    """`doctor` — diagnose the setup: git, store, layer state, tree-sitter, code
    graph, memory counts, AGENTS.md, hooks, and AI provider. Each line says how to
    fix a ✗/⚠. Run this first when something isn't working."""
    s, root = _store()
    from . import llm
    from .graph import extract

    def line(ok, label, detail, fix=""):
        mark = {True: "✓", False: "✗", None: "⚠"}[ok]
        print(f"  {mark} {label}: {detail}" + (f"   → {fix}" if fix and ok is not True else ""))

    print(f"OmniMemory {__version__}  ·  doctor  ·  project: {root.name}")
    print(f"  store: {s.dir}")
    is_repo = gitmeta.is_repo(root)
    line(is_repo, "git repository", "yes" if is_repo else "NOT a git repo",
         "run `git init` (memory is branch-anchored)")
    line(s.get_meta("enabled", True), "memory layer", "ON" if s.get_meta("enabled", True) else "OFF",
         "run `omni-memory on`")
    ts = extract.available()
    line(True if ts else None, "tree-sitter", "installed (exact multi-language graph)" if ts
         else "absent — stdlib backend (Python via ast, JS/TS via regex; approximate)",
         "pip install omni-memory-agent on Python ≥3.10 for the exact graph")
    all_nodes = s.code_graph()[0]
    funcs = sum(1 for n in all_nodes if n["kind"] != "file")
    files = len(all_nodes) - funcs
    line(funcs > 0, "code graph", f"{funcs} symbols ({files} files)",
         "run `omni-memory map`; non-Python needs tree-sitter or the regex backend")
    c = s.counts()
    active = c.get("active", 0)
    stale = s.db.execute("SELECT COUNT(*) n FROM memory WHERE status='active' AND stale=1").fetchone()["n"]
    line(active > 0, "memories", f"{active} active" + (f", ⚠ {stale} stale" if stale else ""),
         "capture some (work a session) or `omni-memory build`")
    try:  # MEASURED provenance: not schema presence, the number we can verify today
        from . import staleness
        rec = staleness.reconcile(s, root)
        if rec["anchored"]:
            ref, loc = int(rec["refetch_coverage"] * 100), int(rec["locator_coverage"] * 100)
            enum = int(rec["source_enumeration_coverage"] * 100)
            detail = (f"{ref}% of {rec['anchored']} anchored memories verified re-fetchable "
                      f"({rec['fresh']} fresh, {rec['drifted']} drifted, "
                      f"{rec['orphaned']} orphaned, {rec['uncheckable']} unverifiable); "
                      f"{loc}% carry a content key; {enum}% deletion-detectable")
            clean = not (rec["orphaned"] or rec["uncheckable"])
            line(True if clean else None, "source integrity", detail,
                 "orphans flagged stale (`omni-memory gc`); re-capture legacy memory to add content keys")
    except Exception:  # noqa: BLE001
        pass
    agents = (root / "AGENTS.md").exists()
    line(agents, "AGENTS.md", "present (cross-IDE context)" if agents else "missing",
         "run `omni-memory bind`")
    settings = root / ".claude" / "settings.json"
    proj_hooked = settings.exists() and "omni_memory hook" in settings.read_text(
        encoding="utf-8", errors="ignore")
    if proj_hooked:
        line(True, "Claude Code hooks", "wired (project settings)")
    elif _plugin_hooks_present():
        line(True, "Claude Code hooks", "wired (marketplace plugin)")
    else:
        line(False, "Claude Code hooks", "not installed",
             "run `omni-memory bind claude-code`, or install the plugin")
    prov = llm.provider()
    line(True if prov else None, "AI provider", prov or "none (headless capture off)",
         "set GEMINI_API_KEY / ANTHROPIC_API_KEY for headless capture")
    return 0


def _force_utf8_io():
    """OmniMemory emits Unicode (◇ ⚠ 🌐 → · in status/inject blocks). A Windows
    console defaults to cp1252 and raises UnicodeEncodeError on print — so make
    stdout/stderr UTF-8. No-op where already UTF-8 or unsupported."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main(argv=None):
    import argparse
    _force_utf8_io()
    p = argparse.ArgumentParser(prog="omni-memory",
                                description="Memory & context layer for coding agents.")
    p.add_argument("--version", action="version", version=f"omni-memory {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("on"); sub.add_parser("off")
    im = sub.add_parser("inject-mode")
    im.add_argument("mode", nargs="?", choices=["auto", "session", "manual"])
    sub.add_parser("branch-aware")
    r = sub.add_parser("remember"); r.add_argument("--kind", default="fact")
    r.add_argument("--global", dest="glob", action="store_true", help="store in the shared ~/.omni-memory (injects everywhere)")
    r.add_argument("--verified", action="store_true", help="confirmed by outcome (trusted, pruned last)")
    r.add_argument("--inferred", action="store_true", help="guessed from code/context (pruned first)")
    r.add_argument("--lock", action="store_true", help="protect: never decays or gets evicted")
    r.add_argument("text", nargs="+")
    lk = sub.add_parser("lock"); lk.add_argument("id")
    ul = sub.add_parser("unlock"); ul.add_argument("id")
    sub.add_parser("share"); sub.add_parser("sync"); sub.add_parser("team")
    sub.add_parser("capture")
    ij = sub.add_parser("inject"); ij.add_argument("query", nargs="*"); ij.add_argument("--file", action="append")
    rc = sub.add_parser("recall"); rc.add_argument("query", nargs="+")
    sub.add_parser("branches")
    fg = sub.add_parser("forget"); fg.add_argument("id")
    us = sub.add_parser("used"); us.add_argument("id", nargs="+")
    gc = sub.add_parser("gc")
    gc.add_argument("--dry-run", action="store_true", help="preview, change nothing")
    gc.add_argument("--purge", action="store_true",
                    help="hard-delete long-quarantined, never-cited memory")
    rs = sub.add_parser("restore"); rs.add_argument("target", help="memory id or branch")
    sub.add_parser("map")
    sub.add_parser("check")
    sub.add_parser("digest")
    bd = sub.add_parser("build")
    bd.add_argument("--no-docs", action="store_true")
    bd.add_argument("--no-ai", action="store_true", help="skip the model pass")
    pr = sub.add_parser("prompt"); pr.add_argument("which", nargs="?", default="build",
                                                   choices=["build", "session"])
    ar = sub.add_parser("artifact"); ar.add_argument("which", nargs="?", default="all",
                                                     choices=["apimap", "linkup", "all"])
    fl = sub.add_parser("flush")
    fl.add_argument("--scope", choices=["all", "memory", "graph"], default="all")
    fl.add_argument("--yes", "-y", action="store_true", help="skip confirmation")
    hk = sub.add_parser("hook")
    hk.add_argument("event", choices=["start", "inject", "capture", "precompact"])
    ky = sub.add_parser("key"); ky.add_argument("provider", choices=["gemini", "anthropic", "openai"])
    ui = sub.add_parser("ui"); ui.add_argument("--port", type=int, default=7777)
    ins = sub.add_parser("install"); ins.add_argument("--platform", default="claude-code")
    bn = sub.add_parser("bind")
    bn.add_argument("ide", nargs="?", default="auto",
                    choices=["auto", "claude-code", "opencode", "antigravity",
                             "cursor", "windsurf"],
                    help="which IDE to bind (default: auto-detect)")
    sub.add_parser("doctor")
    ug = sub.add_parser("usage")
    ug.add_argument("--max-items", type=int, help="max memories injected per prompt")
    ug.add_argument("--budget", type=int, help="max chars of the injected block")
    ex = sub.add_parser("export")
    ex.add_argument("file", nargs="?", help="output path (default: ./omni-memory.json)")
    ex.add_argument("--global", dest="glob", action="store_true", help="the shared ~/.omni-memory store")
    im = sub.add_parser("import")
    im.add_argument("file", nargs="?", help="input path (default: ./omni-memory.json)")
    im.add_argument("--global", dest="glob", action="store_true", help="into the shared ~/.omni-memory store")

    args = p.parse_args(argv)
    dispatch = {
        None: cmd_status, "status": cmd_status, "on": cmd_toggle, "off": cmd_toggle,
        "branch-aware": cmd_branch_aware, "remember": cmd_remember, "capture": cmd_capture,
        "inject": cmd_inject, "inject-mode": cmd_inject_mode,
        "recall": cmd_recall, "branches": cmd_branches,
        "forget": cmd_forget, "used": cmd_used, "gc": cmd_gc, "restore": cmd_restore,
        "lock": cmd_lock, "unlock": cmd_lock,
        "share": cmd_share, "sync": cmd_sync, "team": cmd_team,
        "map": cmd_map, "check": cmd_check, "digest": cmd_digest,
        "build": cmd_build, "prompt": cmd_prompt, "artifact": cmd_artifact,
        "key": cmd_key, "hook": cmd_hook, "ui": cmd_ui, "install": cmd_install,
        "flush": cmd_flush, "bind": cmd_bind, "doctor": cmd_doctor,
        "usage": cmd_usage, "export": cmd_export, "import": cmd_import,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
