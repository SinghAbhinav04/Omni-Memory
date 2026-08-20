"""`omni-memory` / `/omni-memory` command dispatch."""
from __future__ import annotations

import json
import sys
import time as _t
from pathlib import Path

from . import __version__, branch as branchmod, digest, gitmeta, graphbuild, inject
from . import session_memory as sm
from .store import Store, find_project_root


def _store():
    """Open the store for the project the CWD lives in. Every command starts here."""
    root = find_project_root()
    return Store(root), root


def _report_noise_sweep(s):
    """Tell the user when the one-time retro-sweep quarantined legacy noise. Never
    silent: it changed their store, and the undo has to travel with the notice."""
    n = getattr(s, "_noise_swept", 0)
    if n:
        print(f"omni-memory: quarantined {n} memory the current noise filter rejects "
              "(raw chat lines / invented file anchors from an older capture). "
              "Reversible: omni-memory gc --restore <id>", file=sys.stderr)


def cmd_status(args):
    """`status` — layer state, current branch, memory counts, AI provider, store path."""
    s, root = _store()
    _report_noise_sweep(s)
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


# Every tunable setting, in one place: `key -> (default, validator, one-line help)`.
# These were previously a bespoke subcommand each (`inject-mode`, `branch-aware`) plus
# two flags buried in `usage`, which meant no single place listed what could be tuned.
_CONFIG = {
    "inject_mode": ("session", ("auto", "session", "manual"),
                    "how memory reaches the agent: session=seed once then pull "
                    "(default, cheapest) · auto=every prompt · manual=pull only"),
    "branch_aware": (True, bool,
                     "scope memory to the current branch + its base, not all branches"),
    "inject_max_items": (8, int, "max memories in one injected block"),
    "inject_char_budget": (1400, int, "max characters of the injected block"),
    "inject_global_items": (3, int, "cross-project memories layered in from ~/.omni-memory"),
    "chain_complete": (True, bool,
                       "pull a decision's co-anchored gotchas/constraints with it"),
    "auto_import_shared": (True, bool,
                           "auto-load a committed omni-memory.json on a fresh clone"),
}


def _parse_setting(key: str, raw: str):
    """Coerce a CLI string to the type this key declares, or raise ValueError."""
    default, kind, _help = _CONFIG[key]
    if isinstance(kind, tuple):
        if raw not in kind:
            raise ValueError(f"must be one of: {' | '.join(kind)}")
        return raw
    if kind is bool:
        if raw.lower() in ("on", "true", "yes", "1"):
            return True
        if raw.lower() in ("off", "false", "no", "0"):
            return False
        raise ValueError("must be on|off")
    return int(raw)


def cmd_config(args):
    """`config [key] [value]` — show or change a setting. With no arguments it lists
    every tunable with its current value, so what can be adjusted is discoverable in
    one place rather than spread across bespoke subcommands."""
    s, _ = _store()
    key = getattr(args, "key", None)
    if not key:
        print("OmniMemory settings  (omni-memory config <key> <value>)")
        for k, (default, _kind, help_text) in _CONFIG.items():
            cur = s.get_meta(k, default)
            shown = {True: "on", False: "off"}.get(cur, cur)
            print(f"  {k:<20} {str(shown):<9} {help_text}")
        return 0
    if key not in _CONFIG:
        print(f"unknown setting '{key}'. Known: {', '.join(_CONFIG)}")
        return 1
    default, _kind, help_text = _CONFIG[key]
    if getattr(args, "value", None) is not None:
        try:
            s.set_meta(key, _parse_setting(key, args.value))
        except ValueError as e:
            print(f"{key}: {e}")
            return 1
    cur = s.get_meta(key, default)
    shown = {True: "on", False: "off"}.get(cur, cur)
    print(f"{key}: {shown}")
    print(f"  {help_text}")
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
    """`lock <id> [--off]` — pin a memory as constitutional (never decays or gets
    evicted), or release it. For architecture, identity, and standing rules."""
    s, _ = _store()
    on = not getattr(args, "off", False)
    ok = s.set_protected(args.id, on)
    print(f"[+] {'🔒 locked' if on else 'unlocked'} [{args.id}]" if ok
          else f"no memory with id {args.id}")
    return 0 if ok else 1


def cmd_conflicts(args):
    """`conflicts` — memories that contradict each other on the same symbol after a
    branch merge. Resolve with `omni-memory resolve <id> [--keep|--both]`."""
    s, root = _store()
    branchmod.refresh_if_stale(s, root)             # detect merges since last refresh
    conf = s.open_conflicts()
    if not conf:
        print("no open conflicts.")
        return 0
    for c in conf:
        a, b = c["a"], c["b"]
        print(f"\n⚠ conflict on {c['anchor']}  ({', '.join(a['files'][:2])})")
        for m in (a, b):
            who = f" · by {m['author']}" if m.get("author") else ""
            print(f"   [{m['id']}] ({m['branch']}){who}: {m['text']}")
        print(f"   → keep one:  omni-memory resolve <{a['id']}|{b['id']}> --keep"
              f"   ·   both true:  omni-memory resolve {a['id']} --both")
    return 0


def cmd_resolve(args):
    """`resolve <id> [--keep|--both]` — settle every conflict this memory is in.
    --keep: this memory wins (its partner is superseded). --both: keep both."""
    s, _ = _store()
    keep = not getattr(args, "both", False)         # default is --keep (pick this one)
    n = s.resolve_conflict(args.id, keep=keep)
    if not n:
        print(f"[{args.id}] is not in any open conflict.")
        return 1
    print(f"[+] resolved {n} conflict(s) — " + ("kept this memory, superseded the other(s)"
          if keep else "kept both, cleared the flag"))
    return 0


def cmd_history(args):
    """`history <id>` — the supersession lineage of a memory (what it replaced and
    what replaced it), oldest → newest."""
    s, _ = _store()
    chain = s.history(args.id)
    if not chain:
        print(f"no memory with id {args.id}"); return 1
    import time as _t
    for i, m in enumerate(chain):
        arrow = "└─▶ " if i else "    "
        when = _t.strftime("%Y-%m-%d", _t.localtime(m.get("updated") or m.get("created") or 0))
        mark = " (current)" if m["status"] == "active" else f" ({m['status']})"
        print(f"{arrow}[{m['id']}] {when}{mark}: {m['text']}")
    return 0


def cmd_capture(args):
    """`capture [--prompt build|session]` — ingest the agent's extraction JSON from
    stdin. `--prompt` prints the extraction instructions instead, which is the other
    half of the same pipe: `omni-memory capture --prompt` → agent → `capture`."""
    which = getattr(args, "prompt", None)
    if which:
        print(sm.BUILD_PROMPT if which == "build" else sm.EXTRACTION_PROMPT)
        return 0
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
        except Exception as e:  # noqa: BLE001
            print(f"[!] AI pass failed ({e}); falling back to docs.")
            use_ai = False

    # AI-written docs (api-map / linkup). On by default with a key, since a build that
    # read the whole codebase and then declined to write the docs was the surprise the
    # separate `artifact` command existed to undo.
    if not args.no_docs_gen and llm.available():
        try:
            from . import artifacts
            for p in artifacts.generate_all(s, root):
                print(f"[+] artifact → {p}")
        except Exception as e:  # noqa: BLE001
            print(f"[!] artifact generation failed ({e})")

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


def cmd_inject(args):
    """`inject <query>` — print the VERIFIED PROJECT MEMORY block for a request
    (what the agent should treat as ground truth at the start of a task)."""
    s, root = _store()
    if not s.get_meta("enabled", True):
        return 0
    branchmod.refresh_if_stale(s, root)         # a pull always reflects the current code
    block = inject.build_block(s, root, query=" ".join(args.query or []),
                               files=args.file or None, event="pull")
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
    """`check [--graph]` — re-anchor memories against git: flag any whose code changed
    since they were written as ⚠ stale (they keep their content — re-verify, don't
    trust). The code graph is rebuilt first either way, so staleness is symbol/caller-
    precise; `--graph` additionally rebuilds and writes the knowledge graph JSON the
    dashboard reads, and reports the code-graph backend."""
    from . import staleness
    from .graph import build as codegraph, extract
    s, root = _store()
    if getattr(args, "graph", False):
        branchmod.sync_git(s, root)
        g = graphbuild.build_graph(s, root)
        out = s.dir / "graph.json"
        out.write_text(json.dumps(g, indent=2), encoding="utf-8")
        print(f"[+] memory graph: {len(g['nodes'])} nodes, "
              f"{len(g['edges'])} edges → {out}")
    try:
        cg = codegraph.build_code_graph(s, root)
    except Exception:  # noqa: BLE001
        cg = None  # fall back to file-level staleness
    if getattr(args, "graph", False) and cg and cg["files_parsed"]:
        print(f"[+] code graph ({cg['backend']}): {cg['nodes']} symbols, "
              f"{cg['edges']} edges over {cg['files_parsed']} file(s)")
        print(f"    {cg['kinds']}  ·  {cg['rels']}")
        if not extract.available():
            print("    [i] deep multi-language graph needs tree-sitter → "
                  "pip install omni-memory-agent (Python is graphed via stdlib ast).")
    r = staleness.recompute(s, root)
    print(f"[+] checked {r['checked']} anchored memory · {r['stale']} stale · "
          f"{r['cleared']} cleared  ({r['level']}-level)")
    from . import eviction
    sw = eviction.sweep(s, root, dry_run=False, purge=False)
    if sw["quarantined"]:
        print(f"[+] quarantined {len(sw['quarantined'])} dead/false memory "
              f"(reversible: omni-memory gc --restore <id|branch>)")
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


def cmd_systemmap(args):
    """`systemmap [-o FILE]` — write the interactive system map as one self-contained
    HTML file: the implemented architecture as an isometric city, every claim carrying
    a citation and a provenance verdict re-resolved against the code right now.

    Unlike a generated architecture diagram, this one can show where it has gone stale:
    a building whose cited source drifted renders degraded, a deleted source renders
    orphaned. The file makes no network request, so it keeps working after the checkout
    it describes is gone.
    """
    from . import systemmap
    s, root = _store()
    branchmod.refresh_if_stale(s, root)          # the map must reflect the code as it is
    model = systemmap.build(s, root)
    if not model["nodes"]:
        print("nothing to map yet — run `omni-memory check --graph` for the code graph, then "
              "`omni-memory build` (or work a session) to capture memory.")
        return 1
    out = Path(args.out) if getattr(args, "out", None) else root / f"{root.name}-system-map.html"
    out.write_text(systemmap.artifact(s, root), encoding="utf-8")
    st, r = model["stats"], model["repository"]
    v = st["verdicts"]
    print(f"[+] system map → {out}")
    print(f"    {st['buildings']} buildings · {st['routes']} routes · {st['flows']} flows "
          f"· branch {r['branch']} @ {r['commitShort'] or 'no commit'}")
    # Report the decay honestly: a map that only says how much it drew would be the
    # confident-and-wrong artifact this whole design exists to avoid.
    parts = [f"{n} {k.lower()}" for k, n in sorted(v.items()) if n]
    print(f"    evidence: {', '.join(parts)}"
          + (f" · {st['notBindable']} memories not bindable (no source anchor)"
             if st["notBindable"] else ""))
    if v.get("DRIFTED") or v.get("ORPHANED"):
        print("    [i] drifted/orphaned buildings rest on sources that moved or vanished "
              "— re-verify before trusting them.")
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
    hint = {"all": "omni-memory build   (or check --graph)",
            "memory": "omni-memory build",
            "graph": "omni-memory check --graph"}[scope]
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
    """`share [--status]` — export your memories to a committed per-author shard under
    `.omni-memory/team/`, so teammates get them on their next session. Each author
    owns a distinct file, so it merges without conflicts. `--status` reports who has
    contributed to this project's memory instead of writing anything."""
    from . import team
    s, root = _store()
    if getattr(args, "status", False):
        st = team.status(s, root)
        print(f"you: {st['me'] or '(no git identity — set git config user.email)'}")
        print("memory by author:")
        for r in st["by_author"]:
            print(f"  {r['c']:>4}  {r['a']}")
        if st["shards"]:
            print("committed shards: " + ", ".join(st["shards"]))
        if not st["enabled"]:
            print("team sharing not set up — `omni-memory share` writes your shard, "
                  "commit it,")
            print("teammates `git pull` then sync automatically at session start.")
        return 0
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


def cmd_snapshot(args):
    """`snapshot [--in|--out FILE] [--global]` — portable JSON memory, both ways.

    `--out` (default) writes a snapshot; commit it to share memory across clones and
    IDEs. `--in` loads one, skipping ids that already exist, so it is safe to re-run
    and safe to merge several snapshots. Default path is `omni-memory.json` at the repo
    root — the same file `_bootstrap_shared` auto-loads on a fresh clone."""
    s, root = _open_store(args.glob)
    default = find_project_root() / "omni-memory.json"
    if getattr(args, "in_file", None) is not None:
        src = Path(args.in_file) if args.in_file else default
        if not src.exists():
            print(f"no snapshot at {src}  "
                  "(run `omni-memory snapshot` first, or pass a path).")
            return 1
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"[!] couldn't read snapshot: {e}")
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
    data = s.export_memories()
    out = Path(args.out) if getattr(args, "out", None) else default
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[+] exported {len(data['memories'])} memories → {out}")
    if not getattr(args, "out", None):
        print("    tip: commit omni-memory.json to share memory across clones/IDEs.")
    return 0


def cmd_gain(args):
    """`gain [--history] [--reset] [--max-items N] [--budget CHARS]` — what memory
    actually bought you, in tokens, plus the footprint it costs and the knobs to tune it.

    The saving is the difference between the block the agent was handed and what it
    would have had to READ to learn the same things. Both figures are estimates and the
    method is printed alongside them, because a number nobody can check is exactly the
    kind of confident claim this project exists to avoid."""
    from . import savings as sv
    s, root = _store()
    if getattr(args, "reset", False):
        n = s.clear_savings()
        print(f"[+] cleared {n} ledger entries.\n")
    if args.max_items is not None:
        s.set_meta("inject_max_items", args.max_items)
    if args.budget is not None:
        s.set_meta("inject_char_budget", args.budget)
    if args.max_items is not None or args.budget is not None:
        print("[+] updated injection limits.\n")

    if getattr(args, "history", False):
        rows = sv.history(s)
        if not rows:
            print("no pulls recorded yet.")
            return 0
        print(f"{'when':<17}{'event':<10}{'served':>8}{'baseline':>10}{'saved':>9}  mem")
        for r in rows:
            when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(r["ts"] or 0))
            print(f"{when:<17}{r['event']:<10}{sv.human(r['served']):>8}"
                  f"{sv.human(r['baseline']):>10}{sv.human(r['saved']):>9}"
                  f"  {len(r['mem_ids'])} mem · {r['files']} src")
        return 0

    g = sv.summary(s)
    print(f"OmniMemory token savings  ·  project: {root.name}")
    if not g["pulls"]:
        print("  no pulls recorded yet — memory is measured as it is used.")
        print("  run `omni-memory inject \"<question>\"`, or start a session, then re-run.")
    else:
        since = _t.strftime("%Y-%m-%d", _t.localtime(g["first_ts"] or 0))
        print(f"  pulls            : {g['pulls']}  ·  {g['sessions']} session seed(s)"
              f"  ·  since {since}")
        print(f"  served           : {sv.human(g['served']):>8} tok    "
              "what memory actually cost")
        print(f"  baseline (re-read): {sv.human(g['baseline']):>7} tok    "
              "reading the cited sources instead")
        print(f"  saved            : {sv.human(g['saved']):>8} tok    ({g['pct']}%)")
        r = g["recent"]
        if r["pulls"]:
            avg = sv.human(r["saved"] // r["pulls"])
            print(f"  last {g['recent_days']} days      : {sv.human(r['saved']):>8} saved"
                  f"  ·  avg {avg} / pull")

    # Footprint: what memory costs right now. No `event=`, so this measurement pass is
    # never itself recorded as a pull — otherwise looking at the metric would inflate it.
    toks = sv.tokens
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
    print(f"\n  footprint now: seed ~{toks(block)} tok · AGENTS.md ~{toks(ag_block)} tok"
          + (f" · MEMORY.md ~{toks(mem_md.read_text(encoding='utf-8', errors='ignore'))}"
             " tok (@-ref only)" if mem_md.exists() else ""))
    print(f"  settings: max_items={mi}  char_budget={cb}"
          "        tune: gain --max-items 6 --budget 1000")
    from . import llm
    cap = "LLM" if llm.autocapture_ok() else "heuristic (free — no claude -p on autopilot)"
    print(f"  SessionEnd auto-capture: {cap}."
          + ("" if llm.autocapture_ok()
             else " Set OMNI_HEADLESS_LLM=1 or an API key for LLM capture."))
    print(f"\n  method: {g['method']}")
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
    flag (set it false to require an explicit `omni-memory snapshot --in`)."""
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
    _report_noise_sweep(s)                     # stderr, so it can't pollute the block
    mode = s.get_meta("inject_mode", "session")   # session (default) | auto | manual
    if args.event == "start":                  # SessionStart → refresh + ensure AGENTS.md
        s.clear_read_ledger()                  # fresh session: prior-session reads must not bind now
        s.clear_witness()                      # ...and a prior session's pins can't date THIS task's window
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
            block = inject.build_block(s, root, query="", event="session")
            if block:
                print(block)                    # grounds a cold agent; then it pulls on demand
                _mark_injected(s, block)
        return 0
    if args.event == "inject":                 # UserPromptSubmit
        if mode != "auto":                      # pull mode (default): the agent fetches
            return 0                            # memory itself via `omni-memory inject` — no
                                                # per-prompt token cost, memory kept fresh above
        block = inject.build_block(s, root, query=data.get("prompt", ""),
                                   event="prompt")
        # auto mode only: de-dupe so an identical block isn't re-emitted every prompt.
        if block and _should_inject(s, block):
            print(block)                        # stdout is added to the prompt context
        return 0
    if args.event == "read":                   # PostToolUse(Read|Edit|Write) → read ledger
        # Record the digest of what the agent actually READ, so capture can bind a
        # memory to the observed bytes (not the file at session-end) and detect a
        # source that moved between observation and capture (UNBOUND_CAPTURE).
        import os
        ti = data.get("tool_input") or {}
        fp = ti.get("file_path") or ti.get("path") or ""
        if fp:
            try:
                rel = os.path.relpath(fp, root)
                p = root / rel
                if (not rel.startswith("..")           # only files inside the repo
                        and p.is_file() and p.stat().st_size <= 2_000_000):  # skip huge files
                    dig = gitmeta.blob_sha(root, rel)
                    if dig:
                        s.read_ledger_put(rel, dig)
            except Exception:  # noqa: BLE001
                pass
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
            cited = sm.extract_citations(text, ids)
            bumped = s.bump_uses(cited)
            try:
                # VERIFY -> USE, retrospectively. After the fact still matters: it says
                # the memory the agent ACTED on had already moved, so the work may rest
                # on a stale answer. The eager path is `omni-memory used`; this one
                # always runs, so the window can't go unnoticed just because the agent
                # never called it.
                from . import witness
                note = witness.note_on_use(s, root, cited)
                if note:
                    print(f"omni-memory: {note}", file=sys.stderr)
            except Exception:  # noqa: BLE001
                pass
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
    """`used <id>…` — record that memories were cited, and re-verify them AT THE POINT
    OF USE: the sources pinned when they were pulled are re-read now, so a memory that
    went stale mid-task is caught before it is acted on further."""
    s, root = _store()
    n = s.bump_uses(args.id)
    print(f"[+] recorded use of {n} memory")
    from . import witness
    r = witness.verify(s, root, args.id)
    if not r["checked"]:
        return 0
    if not r["valid"]:
        # Never "clean" — we did not check the world, and saying otherwise is the whole
        # failure this window exists to name.
        print("[i] the world was NOT checked: no source was bound when these were pulled "
              f"(sources bound {r['sources_bound']}).")
        return 0
    print(f"    verified at use: {r['summary']} (sources bound {r['sources_bound']}, "
          f"{r['observation_bound']} observation-bound)")
    for s_ in r["stale_at_use"]:
        print(f"    ⚠ STALE AT USE [{s_['mem_id']}] {s_['path']}: moved since it was "
              f"pulled ({s_['pinned']} → {s_['current']}) — revalidate before relying on it")
    for o in r["orphaned_at_use"]:
        print(f"    ⚠ ORPHANED AT USE [{o['mem_id']}] {o['path']}: gone since it was "
              "pulled — re-source, don't revalidate")
    for lim in r["limits"]:
        print(f"    [i] {lim}")
    if r["stale_at_use"] or r["orphaned_at_use"]:
        witness.note_on_use(s, root, args.id)      # durable, so the next session sees it
    return 0


def cmd_gc(args):
    """`gc [--dry-run] [--purge] [--restore <id|branch>]` — garbage-collect dead/false
    memory: quarantine abandoned-branch and stale memories (reversible); --purge
    hard-deletes long-quarantined uncited ones; --restore un-quarantines a memory by id
    or every memory from a branch. Restore lives here because quarantine does."""
    from . import eviction
    s, root = _store()
    target = getattr(args, "restore", None)
    if target:
        n = s.restore(target) or s.restore_branch(target)
        print(f"[+] restored {n} memory" if n
              else "nothing to restore for that id/branch.")
        return 0 if n else 1
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


def _doctor_read_collector(s, root, line):
    """Liveness gate for the observation collector: a hook-based ledger can die
    SILENTLY (bad interpreter, import error → exit 0) while every existing record
    still reads observed. Exit codes can't see that, so we run the read hook exactly
    the way Claude Code invokes it — synthetic stdin through the shell — against a
    real probe file, and confirm it actually WROTE the ledger. OK / not-recording."""
    import subprocess
    from . import install as _inst
    probe = ""
    try:
        probe = (gitmeta._git(root, "ls-files").splitlines() or [""])[0]
    except Exception:  # noqa: BLE001
        pass
    if not probe or not (root / probe).is_file():
        return                                     # nothing to probe with
    try:
        s.read_ledger_del(probe)                   # clear any prior entry so a pass is real
        payload = json.dumps({"tool_name": "Read",
                              "tool_input": {"file_path": str((root / probe).resolve())}})
        r = subprocess.run(_inst._hook_cmd("read"), shell=True, input=payload, text=True,
                           cwd=str(root), capture_output=True, timeout=20)
        alive = s.read_ledger_get(probe) is not None
        out = (r.stdout or "") + (r.stderr or "")
        silent = bool(out.strip()) and not alive   # confessed an error but exited 0-ish
        s.read_ledger_del(probe)                    # clean the probe entry either way
        if alive:
            line(True, "read collector", "hook executes and records the read ledger")
        else:
            why = "SILENT-FAIL (ran, wrote nothing — check `python3` runs your hooks / import errors)" \
                  if silent else "not recording — new memory can't be `observed`, only `declared`"
            line(None, "read collector", why,
                 "wire it: `omni-memory bind`; verify `python3` resolves to a real interpreter")
    except Exception:  # noqa: BLE001
        try:
            s.read_ledger_del(probe)
        except Exception:  # noqa: BLE001
            pass


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
         "run `omni-memory check --graph`; non-Python needs tree-sitter or the regex backend")
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
            nb = rec.get("not_bindable", 0)          # reported BESIDE, never inside the %
            detail = (f"{ref}% of {rec['anchored']} bindable memories verified re-fetchable "
                      f"({rec['fresh']} fresh, {rec['drifted']} drifted, "
                      f"{rec['orphaned']} orphaned, {rec['uncheckable']} unverifiable); "
                      f"{loc}% carry a content key; {enum}% deletion-detectable"
                      + (f"; {nb} not-bindable (no addressable source — beside the denominator)" if nb else ""))
            clean = not (rec["orphaned"] or rec["uncheckable"])
            line(True if clean else None, "source integrity", detail,
                 "orphans flagged stale (`omni-memory gc`); re-capture legacy memory to add content keys")
            # observation binding is a SEPARATE axis from refetchability
            obs = int(rec.get("observation_binding_coverage", 0) * 100)
            unb = rec.get("unbound", 0)
            line(True if not unb else None, "observation binding",
                 f"{obs}% observed (bound to bytes actually read), rest declared (capture-time)"
                 + (f"; ⚠ {unb} UNBOUND_CAPTURE (source moved before capture — re-read & re-capture)" if unb else ""),
                 "install the read hook (`omni-memory bind`) so capture binds to what was read")
    except Exception:  # noqa: BLE001
        pass
    _doctor_read_collector(s, root, line)          # WOULD the read hook work if invoked?
    try:
        # ...and DID it actually run? Different question, and it needs a witness the
        # collector doesn't own — the runtime's own session transcript. A hook that was
        # never registered leaves an empty ledger that looks exactly like a quiet
        # session, while every stored record keeps claiming `observed`.
        from . import collector
        lv = collector.liveness(s, root)
        line({"OK": True, "FAIL": False, "SKIP": None}[lv["verdict"]],
             "collector ran (external witness)", f"{lv['verdict']} — {lv['why']}",
             lv["remedy"])
    except Exception:  # noqa: BLE001
        pass
    try:                                           # identifier contract, re-measured now
        from . import identifier
        ic = identifier.identifier_contract(s)
        if ic["keys"]:
            # off-form ids are a real failure (foreign ids arrive via team shards);
            # the fold cost is a pre-flight, and its zero is reported three-valued so an
            # undersized population can't read as a clean bill of health.
            detail = (f"{ic['keys']} ids, {ic['verdict'].lower().replace('_', ' ')} "
                      f"({ic['why']}); headroom {ic['headroom_chars']} char(s)")
            if ic["off_form_count"]:
                detail = (f"⚠ {ic['off_form_count']} id(s) outside the declared form "
                          f"({identifier.DECLARED}): "
                          f"{', '.join(ic['off_form'][:4])} · " + detail)
            line(not ic["off_form_count"], "identifier contract", detail,
                 "off-form ids usually arrive via an imported/team shard from another "
                 "version — re-export it with a matching omni-memory")
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

    # -- setup & state --------------------------------------------------------
    sub.add_parser("status", help="layer state, branch, memory counts, store path")
    sub.add_parser("doctor", help="diagnose the setup; every line says how to fix it")
    cf = sub.add_parser("config", help="show or change a setting (no args = list all)")
    cf.add_argument("key", nargs="?", help="setting name")
    cf.add_argument("value", nargs="?", help="new value")
    sub.add_parser("on", help="enable the memory layer")
    sub.add_parser("off", help="disable the memory layer")
    bn = sub.add_parser("bind", help="wire an IDE: session hooks + AGENTS.md")
    bn.add_argument("ide", nargs="?", default="auto",
                    choices=["auto", "claude-code", "opencode", "antigravity",
                             "cursor", "windsurf"],
                    help="which IDE to bind (default: auto-detect)")
    ky = sub.add_parser("key", help="store a model API key (chmod 600, gitignored)")
    ky.add_argument("provider", choices=["gemini", "anthropic", "openai"])

    # -- using memory ---------------------------------------------------------
    r = sub.add_parser("remember", help="add one memory by hand")
    r.add_argument("--kind", default="fact")
    r.add_argument("--global", dest="glob", action="store_true",
                   help="store in the shared ~/.omni-memory (injects everywhere)")
    r.add_argument("--verified", action="store_true",
                   help="confirmed by outcome (trusted, pruned last)")
    r.add_argument("--inferred", action="store_true",
                   help="guessed from code/context (pruned first)")
    r.add_argument("--lock", action="store_true",
                   help="protect: never decays or gets evicted")
    r.add_argument("text", nargs="+")
    rc = sub.add_parser("recall", help="search memory instead of grepping")
    rc.add_argument("query", nargs="+")
    ij = sub.add_parser("inject", help="print the VERIFIED PROJECT MEMORY block")
    ij.add_argument("query", nargs="*")
    ij.add_argument("--file", action="append")
    us = sub.add_parser("used", help="record citation + re-verify at the point of use")
    us.add_argument("id", nargs="+")
    fg = sub.add_parser("forget", help="drop one memory")
    fg.add_argument("id")
    lk = sub.add_parser("lock", help="pin a memory as constitutional (never evicted)")
    lk.add_argument("id")
    lk.add_argument("--off", action="store_true", help="release it instead")

    # -- keeping it fresh -----------------------------------------------------
    bd = sub.add_parser("build", help="bootstrap memory + docs from the repo")
    bd.add_argument("--no-docs", action="store_true", help="skip the markdown scan")
    bd.add_argument("--no-ai", action="store_true", help="skip the model pass")
    bd.add_argument("--no-docs-gen", action="store_true",
                    help="skip generating the api-map / linkup artifacts")
    ck = sub.add_parser("check", help="re-anchor vs git; flag ⚠ stale memories")
    ck.add_argument("--graph", action="store_true",
                    help="also rebuild + write the knowledge graph the dashboard reads")
    cp = sub.add_parser("capture", help="ingest extraction JSON from stdin")
    cp.add_argument("--prompt", nargs="?", const="build",
                    choices=["build", "session"],
                    help="print the extraction instructions instead of ingesting")
    gc = sub.add_parser("gc", help="quarantine dead/false memory (reversible)")
    gc.add_argument("--dry-run", action="store_true", help="preview, change nothing")
    gc.add_argument("--purge", action="store_true",
                    help="hard-delete long-quarantined, never-cited memory")
    gc.add_argument("--restore", metavar="ID|BRANCH",
                    help="un-quarantine a memory id, or a whole branch")
    fl = sub.add_parser("flush", help="wipe the store so it can be rebuilt")
    fl.add_argument("--scope", choices=["all", "memory", "graph"], default="all")
    fl.add_argument("--yes", "-y", action="store_true", help="skip confirmation")

    # -- seeing it ------------------------------------------------------------
    smp = sub.add_parser("systemmap", help="self-contained HTML architecture map")
    smp.add_argument("-o", "--out", help="output path (default <repo>-system-map.html)")
    ui = sub.add_parser("ui", help="local dashboard")
    ui.add_argument("--port", type=int, default=7777)
    gn = sub.add_parser("gain", help="tokens saved by memory, + footprint and tuning")
    gn.add_argument("--history", action="store_true", help="recent pulls, one per line")
    gn.add_argument("--reset", action="store_true", help="clear the savings ledger")
    gn.add_argument("--max-items", type=int, help="max memories injected per prompt")
    gn.add_argument("--budget", type=int, help="max chars of the injected block")
    sub.add_parser("branches", help="git topology + per-branch memory")

    # -- merges / conflicts ---------------------------------------------------
    sub.add_parser("conflicts", help="memories that contradict after a branch merge")
    rs = sub.add_parser("resolve", help="settle a conflict")
    rs.add_argument("id")
    rs.add_argument("--keep", action="store_true", help="this memory wins (default)")
    rs.add_argument("--both", action="store_true", help="keep both; just clear the flag")
    hs = sub.add_parser("history", help="supersession lineage of a memory")
    hs.add_argument("id")

    # -- team / portability ---------------------------------------------------
    sh = sub.add_parser("share", help="write your committed per-author memory shard")
    sh.add_argument("--status", action="store_true",
                    help="who contributed what, instead of writing a shard")
    sub.add_parser("sync", help="pull teammates' shared memory")
    sn = sub.add_parser("snapshot", help="portable JSON memory, in or out")
    sn.add_argument("--out", nargs="?", const="", metavar="FILE",
                    help="write a snapshot (default: ./omni-memory.json)")
    sn.add_argument("--in", dest="in_file", nargs="?", const="", metavar="FILE",
                    help="load a snapshot (default: ./omni-memory.json)")
    sn.add_argument("--global", dest="glob", action="store_true",
                    help="the shared ~/.omni-memory store")

    # -- machine entrypoint (hidden: the IDE calls this, not you) -------------
    # The event names are a contract: `install._hooks_block()` and the published
    # plugin's hooks.json reference them as strings. Renaming one silently unwires
    # every already-installed integration.
    # (no `help=` — argparse lists only subparsers that declare one, and
    # `help=SUPPRESS` renders literally as "==SUPPRESS==" on Python 3.9.)
    hk = sub.add_parser("hook")
    hk.add_argument("event",
                    choices=["start", "inject", "capture", "precompact", "read"])

    args = p.parse_args(argv)
    dispatch = {
        None: cmd_status, "status": cmd_status, "doctor": cmd_doctor,
        "config": cmd_config, "on": cmd_toggle, "off": cmd_toggle,
        "bind": cmd_bind, "key": cmd_key,
        "remember": cmd_remember, "recall": cmd_recall, "inject": cmd_inject,
        "used": cmd_used, "forget": cmd_forget, "lock": cmd_lock,
        "build": cmd_build, "check": cmd_check, "capture": cmd_capture,
        "gc": cmd_gc, "flush": cmd_flush,
        "systemmap": cmd_systemmap, "ui": cmd_ui, "gain": cmd_gain,
        "branches": cmd_branches,
        "conflicts": cmd_conflicts, "resolve": cmd_resolve, "history": cmd_history,
        "share": cmd_share, "sync": cmd_sync, "snapshot": cmd_snapshot,
        "hook": cmd_hook,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
