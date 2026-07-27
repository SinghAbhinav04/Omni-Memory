"""`omni-memory` / `/omni-memory` command dispatch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__, branch as branchmod, digest, gitmeta, graphbuild, inject
from . import session_memory as sm
from .store import Store, find_project_root


def _store():
    root = find_project_root()
    return Store(root), root


def cmd_status(args):
    s, root = _store()
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
    s, _ = _store()
    s.set_meta("enabled", args.cmd == "on")
    print(f"OmniMemory {'enabled' if args.cmd == 'on' else 'disabled'}.")
    return 0


def cmd_branch_aware(args):
    s, _ = _store()
    new = not s.get_meta("branch_aware", True)
    s.set_meta("branch_aware", new)
    print(f"branch-aware: {'ON' if new else 'OFF'}")
    return 0


def cmd_remember(args):
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
    s, root = _store()
    if not s.get_meta("enabled", True):
        return 0
    block = inject.build_block(s, root, query=" ".join(args.query or []),
                               files=args.file or None)
    if block:
        print(block)
    return 0


def cmd_recall(args):
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


def cmd_hook(args):
    """Claude Code hook entrypoint (reads the event JSON on stdin)."""
    try:
        data = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        data = {}
    s, root = _store()
    if not s.get_meta("enabled", True):
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
    from . import install
    return install.install(platform=args.platform)


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="omni-memory",
                                description="Memory & context layer for coding agents.")
    p.add_argument("--version", action="version", version=f"omni-memory {__version__}")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("status")
    sub.add_parser("on"); sub.add_parser("off")
    sub.add_parser("branch-aware")
    r = sub.add_parser("remember"); r.add_argument("--kind", default="fact"); r.add_argument("text", nargs="+")
    sub.add_parser("capture")
    ij = sub.add_parser("inject"); ij.add_argument("query", nargs="*"); ij.add_argument("--file", action="append")
    rc = sub.add_parser("recall"); rc.add_argument("query", nargs="+")
    sub.add_parser("branches")
    fg = sub.add_parser("forget"); fg.add_argument("id")
    us = sub.add_parser("used"); us.add_argument("id", nargs="+")
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
    hk = sub.add_parser("hook"); hk.add_argument("event", choices=["inject", "capture"])
    ky = sub.add_parser("key"); ky.add_argument("provider", choices=["gemini", "anthropic", "openai"])
    ui = sub.add_parser("ui"); ui.add_argument("--port", type=int, default=7777)
    ins = sub.add_parser("install"); ins.add_argument("--platform", default="claude-code")

    args = p.parse_args(argv)
    dispatch = {
        None: cmd_status, "status": cmd_status, "on": cmd_toggle, "off": cmd_toggle,
        "branch-aware": cmd_branch_aware, "remember": cmd_remember, "capture": cmd_capture,
        "inject": cmd_inject, "recall": cmd_recall, "branches": cmd_branches,
        "forget": cmd_forget, "used": cmd_used, "map": cmd_map, "check": cmd_check,
        "digest": cmd_digest,
        "build": cmd_build, "prompt": cmd_prompt, "artifact": cmd_artifact,
        "key": cmd_key, "hook": cmd_hook, "ui": cmd_ui, "install": cmd_install,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
