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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import branch as branchmod, gitmeta, graphbuild
from .store import Store, find_project_root

STATIC = Path(__file__).parent / "static" / "index.html"


def _handler(store: Store, root: Path):
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
                branchmod.sync_git(store, root)
                return self._send(200, json.dumps({
                    "branches": store.branches(),
                    "current": gitmeta.current_branch(root),
                    "default": store.get_meta("default_branch", "main")}))
            if u.path == "/api/commits":
                return self._send(200, json.dumps(store.commits()))
            if u.path == "/api/codegraph":
                nodes, edges = store.code_graph()
                return self._send(200, json.dumps({"nodes": nodes, "edges": edges}))
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


def run_ui(port: int = 7777) -> int:
    root = find_project_root()
    store = Store(root)
    branchmod.sync_git(store, root)
    srv = ThreadingHTTPServer(("127.0.0.1", port), _handler(store, root))
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
    return 0
