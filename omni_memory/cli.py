"""`omni-memory` / `/omni-memory` command dispatch."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import __version__, branch as branchmod, gitmeta, graphbuild, inject
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
    print(f"OmniMemory {__version__}  ·  project: {root.name}")
    print(f"  layer: {'ON' if on else 'OFF'}   branch-aware: {'ON' if ba else 'OFF'}")
    print(f"  branch: {cur}   memories: {c.get('active',0)} active, "
          f"{c.get('merged',0)} merged, {c.get('superseded',0)} superseded")
    print(f"  store: {s.dir}")
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
    print(f"[+] remembered [{m.id}] ({m.kind}, branch {m.branch}): {m.text}")
    return 0


def cmd_capture(args):
    """Ingest the agent's extraction JSON (stdin) → memories."""
    s, root = _store()
    raw = sys.stdin.read()
    n = sm.capture_from_json(s, root, raw, source="session")
    print(f"[+] captured {n} mem  (branch {gitmeta.current_branch(root)})")
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
        print(f"[{m['id']}] {m['kind']} ({m['branch']}): {m['text']}{where}")
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
    print(f"[+] graph: {len(g['nodes'])} nodes, {len(g['edges'])} edges → {out}")
    print("    (P1: augment with graphify AST code graph)")
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
    sub.add_parser("map")
    ui = sub.add_parser("ui"); ui.add_argument("--port", type=int, default=7777)
    ins = sub.add_parser("install"); ins.add_argument("--platform", default="claude-code")

    args = p.parse_args(argv)
    dispatch = {
        None: cmd_status, "status": cmd_status, "on": cmd_toggle, "off": cmd_toggle,
        "branch-aware": cmd_branch_aware, "remember": cmd_remember, "capture": cmd_capture,
        "inject": cmd_inject, "recall": cmd_recall, "branches": cmd_branches,
        "forget": cmd_forget, "map": cmd_map, "ui": cmd_ui, "install": cmd_install,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
