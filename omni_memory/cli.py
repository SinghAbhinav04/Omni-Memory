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
    print(f"OmniMemory {__version__}  ·  project: {root.name}")
    print(f"  layer: {'ON' if on else 'OFF'}   branch-aware: {'ON' if ba else 'OFF'}   AI: {ai}")
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


def cmd_remember(args):
    """`remember <text> [--kind] [--global]` — add one memory by hand. With
    --global it goes to the shared ~/.omni-memory store and injects into EVERY
    project (your standing preferences/knowledge)."""
    if getattr(args, "glob", False):
        from .store import Store, Memory, global_dir
        gs = Store(exact_dir=global_dir())
        m = gs.add_memory(Memory(text=" ".join(args.text), kind=args.kind,
                                 branch="global", source="manual"))
        print(f"[+] remembered GLOBALLY [{m.id}] ({m.kind}): {m.text}")
        return 0
    s, root = _store()
    m = sm.remember(s, root, " ".join(args.text), kind=args.kind, source="manual")
    digest.write_digest(s)
    print(f"[+] remembered [{m.id}] ({m.kind}, branch {m.branch}): {m.text}")
    return 0


def cmd_capture(args):
    """Ingest the agent's extraction JSON (stdin) → memories."""
    s, root = _store()
    raw = sys.stdin.read()
    n = sm.capture_from_json(s, root, raw, source="session")
    digest.write_digest(s)
    print(f"[+] captured {n} mem  (branch {gitmeta.current_branch(root)}); digest updated")
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
    g = graphbuild.build_graph(s, root)
    (s.dir / "graph.json").write_text(json.dumps(g, indent=2))

    ai_added = 0
    use_ai = llm.available() and not args.no_ai
    if use_ai:
        print(f"[*] AI pass via {llm.provider()} — reading the codebase …")
        try:
            ctx = context.gather(root)
            items = llm.extract_memories(sm.BUILD_PROMPT, ctx)
            ai_added = sm.remember_many(s, root, items, source="ai-build")
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
    out.write_text(json.dumps(g, indent=2))
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


def _read_transcript(path):
    """Claude Code transcript is JSONL; flatten to 'role: text' for extraction."""
    from pathlib import Path
    if not path or not Path(path).exists():
        return ""
    out = []
    for line in Path(path).read_text(errors="ignore").splitlines():
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
    return "\n".join(out)[-140_000:]


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


def cmd_export(args):
    """`export [file] [--global]` — write a portable JSON snapshot of memories.
    Default target is `omni-memory.json` at the repo root; commit it to share the
    memory with teammates / other clones, or move it to another machine/IDE."""
    s, root = _open_store(args.glob)
    data = s.export_memories()
    out = Path(args.file) if args.file else (find_project_root() / "omni-memory.json")
    out.write_text(json.dumps(data, indent=2))
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
        data = json.loads(src.read_text())
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
        t = ag.read_text(errors="ignore")
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
        print(f"  MEMORY.md (@-ref)    : ~{toks(mem_md.read_text(errors='ignore')):>4} tok   · only if you @-reference it")
    print(f"\n  settings: max_items={mi}  char_budget={cb}")
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
        n = s.import_memories(json.loads(shared.read_text()), source="shared")
        if n:
            digest.write_digest(s)
            print(f"omni-memory: loaded {n} shared memories from omni-memory.json "
                  "(marked ↗external — verify before trusting). Disable: set "
                  "auto_import_shared=false.", file=sys.stderr)
    except Exception:  # noqa: BLE001
        pass


def _run_hook(args):
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        data = {}
    s, root = _store()
    if not s.get_meta("enabled", True):
        return 0
    if args.event == "start":                  # SessionStart → refresh + ensure AGENTS.md
        _bootstrap_shared(s, root)             # fresh clone → load committed memory
        # Rebuild ONLY if git state changed since last time (the code graph is
        # persisted in SQLite) — so opening a session doesn't re-parse the whole
        # repo when nothing moved. Memory injected below comes from the store, not
        # from re-reading files.
        branchmod.refresh_if_stale(s, root)
        from . import agentsmd
        agentsmd.write(s, root)
        block = inject.build_block(s, root, query="")
        if block:
            print(block)                        # seed the session context
        return 0
    if args.event == "inject":                 # UserPromptSubmit → add memory to context
        block = inject.build_block(s, root, query=data.get("prompt", ""))
        if block:
            print(block)                        # stdout is added to the prompt context
        return 0
    if args.event == "capture":                # SessionEnd/Stop → extract + store
        text = _read_transcript(data.get("transcript_path"))
        if text:
            n = sm.capture_from_json(s, root, text, source="session")
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
            print(f"omni-memory: captured {n} memories, +{bumped} citations",
                  file=sys.stderr)
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
    print(f"[+] {verb} {len(sw['quarantined'])} memory")
    for c in sw["quarantined"][:30]:
        print(f"    ⊘ [{c['id']}] {c['branch']} · {c['reason']} "
              f"(score {c['score']}): {c['text']}")
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
            creds = _json.loads(llm.CREDS.read_text())
        except Exception:  # noqa: BLE001
            creds = {}
    creds[env] = val
    llm.CREDS.write_text(_json.dumps(creds, indent=2))
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
    line(True if ts else None, "tree-sitter", "installed (multi-language graph)" if ts
         else "absent — Python-only via stdlib ast", "pip install omni-memory-agent on Python ≥3.10")
    nodes = len(s.code_graph()[0])
    line(nodes > 0, "code graph", f"{nodes} symbols", "run `omni-memory map`")
    c = s.counts()
    active = c.get("active", 0)
    stale = s.db.execute("SELECT COUNT(*) n FROM memory WHERE status='active' AND stale=1").fetchone()["n"]
    line(active > 0, "memories", f"{active} active" + (f", ⚠ {stale} stale" if stale else ""),
         "capture some (work a session) or `omni-memory build`")
    agents = (root / "AGENTS.md").exists()
    line(agents, "AGENTS.md", "present (cross-IDE context)" if agents else "missing",
         "run `omni-memory bind`")
    settings = root / ".claude" / "settings.json"
    hooked = settings.exists() and "omni_memory hook" in settings.read_text()
    line(hooked, "Claude Code hooks", "wired" if hooked else "not installed",
         "run `omni-memory bind claude-code`")
    prov = llm.provider()
    line(True if prov else None, "AI provider", prov or "none (headless capture off)",
         "set GEMINI_API_KEY / ANTHROPIC_API_KEY for headless capture")
    return 0


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="omni-memory",
                                description="Memory & context layer for coding agents.")
    p.add_argument("--version", action="version", version=f"omni-memory {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("on"); sub.add_parser("off")
    sub.add_parser("branch-aware")
    r = sub.add_parser("remember"); r.add_argument("--kind", default="fact")
    r.add_argument("--global", dest="glob", action="store_true", help="store in the shared ~/.omni-memory (injects everywhere)")
    r.add_argument("text", nargs="+")
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
    hk = sub.add_parser("hook"); hk.add_argument("event", choices=["start", "inject", "capture"])
    ky = sub.add_parser("key"); ky.add_argument("provider", choices=["gemini", "anthropic", "openai"])
    ui = sub.add_parser("ui"); ui.add_argument("--port", type=int, default=7777)
    ins = sub.add_parser("install"); ins.add_argument("--platform", default="claude-code")
    bn = sub.add_parser("bind")
    bn.add_argument("ide", nargs="?", default="auto",
                    choices=["auto", "claude-code", "antigravity"],
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
        "inject": cmd_inject, "recall": cmd_recall, "branches": cmd_branches,
        "forget": cmd_forget, "used": cmd_used, "gc": cmd_gc, "restore": cmd_restore,
        "map": cmd_map, "check": cmd_check, "digest": cmd_digest,
        "build": cmd_build, "prompt": cmd_prompt, "artifact": cmd_artifact,
        "key": cmd_key, "hook": cmd_hook, "ui": cmd_ui, "install": cmd_install,
        "flush": cmd_flush, "bind": cmd_bind, "doctor": cmd_doctor,
        "usage": cmd_usage, "export": cmd_export, "import": cmd_import,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
