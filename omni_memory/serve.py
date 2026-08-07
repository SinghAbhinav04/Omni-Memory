"""Local dashboard server + JSON API.

P0 uses the stdlib http.server so `omni-memory ui` runs with zero extra deps.
The same data is exposed over MCP for agents (P1, see docs). Endpoints:
  GET /                → dashboard
  GET /api/memories    → active memories (?branch=&kind=&q=)
  GET /api/graph       → knowledge graph {nodes, edges}
  GET /api/branches    → git topology
  GET /api/commits     → commits
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import branch as branchmod, gitmeta, graphbuild
from .store import Store, find_project_root

STATIC = Path(__file__).parent / "static" / "index.html"

# Shared with the dashboard: last known git fingerprint + when the watcher last
# rebuilt. The client polls /api/state and re-renders when `sig` moves.
_STATE = {"sig": "", "refreshed_at": 0.0}


def _handler(root: Path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                return self._route()
            except Exception as e:  # noqa: BLE001 — never drop the connection
                try:
                    self._send(500, json.dumps({"error": str(e)}))
                except Exception:  # noqa: BLE001
                    pass

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(n) if n else b""
                return json.loads(raw or b"{}")
            except Exception:  # noqa: BLE001
                return {}

        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            try:
                store = Store(root)
                if u.path == "/api/flush":
                    scope = q.get("scope", ["all"])[0]
                    counts = store.flush(scope)
                    _refresh(root)  # rebuild so emptied views repopulate from disk
                    return self._send(200, json.dumps({"ok": True, "flushed": counts}))
                if u.path == "/api/memory/add":
                    b = self._body()
                    from .store import Memory
                    cur, base = branchmod.scope(store, root)
                    m = store.add_memory(Memory(
                        text=(b.get("text") or "").strip(),
                        kind=b.get("kind") or "fact",
                        branch=(None if cur == "*" else cur) or "main",
                        files=b.get("files") or [], source="dashboard"))
                    _sync_docs(store, root)
                    return self._send(200, json.dumps({"ok": True, "id": m.id}))
                if u.path == "/api/memory/update":
                    b = self._body()
                    ok = store.update_memory(b.get("id", ""), text=b.get("text"),
                                             kind=b.get("kind"), files=b.get("files"))
                    _sync_docs(store, root)
                    return self._send(200 if ok else 404,
                                      json.dumps({"ok": ok}))
                if u.path == "/api/memory/delete":
                    b = self._body()
                    ok = store.forget(b.get("id", ""))
                    _sync_docs(store, root)
                    return self._send(200 if ok else 404, json.dumps({"ok": ok}))
                if u.path == "/api/memory/reanchor":
                    b = self._body()
                    head = gitmeta._git(root, "rev-parse", "HEAD")[:12]
                    ok = store.reanchor_memory(b.get("id", ""), commit_range=head)
                    _sync_docs(store, root)
                    return self._send(200 if ok else 404, json.dumps({"ok": ok}))
                if u.path == "/api/doc/save":
                    b = self._body()
                    name = b.get("name", "")
                    if name not in ("MEMORY.md", "api-map.md", "linkup.md"):
                        return self._send(400, json.dumps({"error": "bad doc name"}))
                    (store.dir / name).write_text(b.get("content", ""))
                    return self._send(200, json.dumps({"ok": True}))
                return self._send(404, json.dumps({"error": "not found"}))
            except Exception as e:  # noqa: BLE001
                return self._send(500, json.dumps({"error": str(e)}))

        def _route(self):
            # A fresh Store (SQLite connection) per request: the threading server
            # runs requests on worker threads, and one shared connection isn't
            # safe for concurrent use (caused intermittent "Failed to fetch").
            store = Store(root)
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ("/", "/index.html"):
                html = STATIC.read_text() if STATIC.exists() else "<h1>OmniMemory</h1>"
                return self._send(200, html, "text/html")
            if u.path == "/api/memories":
                cur, base = branchmod.scope(store, root)
                mems = store.memories(
                    branch=None if cur == "*" else cur, base=base,
                    kinds=q.get("kind"), query=(q.get("q", [""])[0]), limit=500)
                return self._send(200, json.dumps(mems))
            if u.path == "/api/graph":
                return self._send(200, json.dumps(graphbuild.build_graph(store, root)))
            if u.path == "/api/branches":
                # Read cached topology (synced at startup). Re-syncing on every
                # request re-ran the full git snapshot + staleness recompute,
                # which hung the Repo Graph tab on large repos. Pass ?sync=1 to
                # force a refresh.
                if q.get("sync"):
                    try:
                        branchmod.sync_git(store, root)
                    except Exception:  # noqa: BLE001
                        pass
                return self._send(200, json.dumps({
                    "branches": store.branches(),
                    "current": gitmeta.current_branch(root),
                    "default": store.get_meta("default_branch", "main")}))
            if u.path == "/api/state":
                # Tiny endpoint the dashboard polls to know when to re-render.
                return self._send(200, json.dumps(_STATE))
            if u.path == "/api/meta":
                # Static-ish project facts the UI needs once (e.g. absolute repo
                # path so it can build vscode://file/… deep links to symbols).
                from . import __version__
                return self._send(200, json.dumps({
                    "root": str(root.resolve()),
                    "default_branch": store.get_meta("default_branch", "main"),
                    "version": __version__}))
            if u.path == "/api/search":
                return self._send(200, json.dumps(
                    store.search(q.get("q", [""])[0])))
            if u.path == "/api/commits":
                return self._send(200, json.dumps(store.commits()))
            if u.path == "/api/codegraph":
                nodes, edges = store.code_graph()
                return self._send(200, json.dumps({"nodes": nodes, "edges": edges}))
            if u.path == "/api/symbol":
                sid = q.get("id", [""])[0]
                dossier = store.symbol_dossier(sid)
                if dossier is None:
                    return self._send(404, json.dumps({"error": "unknown symbol"}))
                from .graph import extract
                dossier["emits"] = [c for c in dossier.get("calls", [])
                                    if extract.is_emit(c)]
                return self._send(200, json.dumps(dossier))
            if u.path == "/api/docs":
                docs = [{"name": n, "exists": (store.dir / n).exists()}
                        for n in ("MEMORY.md", "api-map.md", "linkup.md")]
                return self._send(200, json.dumps(docs))
            if u.path == "/api/doc":
                name = q.get("name", [""])[0]
                f = store.dir / name
                if name in ("MEMORY.md", "api-map.md", "linkup.md") and f.exists():
                    return self._send(200, f.read_text(), "text/plain; charset=utf-8")
                return self._send(404, "not found", "text/plain")
            return self._send(404, json.dumps({"error": "not found"}))

    return H


def _sync_docs(store: Store, root: Path) -> None:
    """After a dashboard memory edit, regenerate the derived artifacts so the
    knowledge base and the cross-IDE AGENTS.md reflect the change immediately."""
    try:
        from . import digest, agentsmd
        digest.write_digest(store)
        agentsmd.write(store, root)
    except Exception:  # noqa: BLE001
        pass


def _refresh(root: Path) -> None:
    """One full rebuild (code graph + topology + staleness), guarded. Publishes
    the fresh signature only after the build completes, so the dashboard's poll
    never re-renders against a half-built graph."""
    try:
        branchmod.full_refresh(Store(root), root)
        _STATE["sig"] = gitmeta.state_signature(root)
        _STATE["refreshed_at"] = time.time()
    except Exception:  # noqa: BLE001
        pass


def _watch(root: Path, stop: threading.Event,
           interval: float = 2.0, cooldown: float = 3.0) -> None:
    """Poll git state and rebuild whenever it moves, so the dashboard stays live
    with zero manual commands. Throttled rather than debounced: the first change
    rebuilds right away (a new branch or a save shows within ~a poll), then we
    cool down briefly so a burst of rapid edits coalesces into one rebuild
    instead of thrashing. Runs an initial refresh on startup regardless."""
    last_sig = None
    last_build = 0.0
    while not stop.is_set():
        try:
            sig = gitmeta.state_signature(root)
        except Exception:  # noqa: BLE001
            stop.wait(interval)
            continue
        now = time.time()
        if sig != last_sig and (now - last_build) >= cooldown:
            _refresh(root)               # rebuild (also republishes _STATE)
            last_sig = sig
            last_build = time.time()
        stop.wait(interval)


def run_ui(port: int = 7777) -> int:
    root = find_project_root()
    # Continuously keep the code graph, branches, and staleness fresh in the
    # background; the dashboard just reads the results and re-renders on change.
    stop = threading.Event()
    threading.Thread(target=lambda: _watch(root, stop), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _handler(root))
    url = f"http://127.0.0.1:{port}/"
    print(f"[+] OmniMemory dashboard → {url}  (Ctrl+C to stop)")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] dashboard stopped.")
    finally:
        stop.set()
    return 0
