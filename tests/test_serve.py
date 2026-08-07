"""HTTP integration: the dashboard's JSON API (read + write endpoints)."""
import json
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from omni_memory import serve, branch as branchmod
from omni_memory.store import Store, Memory


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def server(store, repo):
    branchmod.full_refresh(store, repo)              # code graph + branches present
    store.add_memory(Memory(text="checkout uses Stripe PaymentIntents",
                            kind="decision", branch="main", files=["svc.py"]))
    port = _free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), serve._handler(repo))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _get(base, path, timeout=20):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def _post(base, path, body, timeout=20):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def test_read_endpoints(server):
    assert "sig" in _get(server, "/api/state")     # watcher isn't running in tests
    assert _get(server, "/api/meta")["root"]
    assert _get(server, "/api/memories")
    assert _get(server, "/api/codegraph")["nodes"]
    assert "branches" in _get(server, "/api/branches")
    assert _get(server, "/api/overview")["memories"]["active"] >= 1


def test_search_endpoint(server):
    r = _get(server, "/api/search?q=stripe")
    assert any("Stripe" in m["text"] for m in r["memories"])


def test_symbol_endpoint(server):
    cg = _get(server, "/api/codegraph")
    sid = next(n["id"] for n in cg["nodes"] if n["name"] == "create_order")
    d = _get(server, "/api/symbol?id=" + urllib.parse.quote(sid))
    assert "ValidationError" in d["raises"]
    assert d["signature"].startswith("(self, user, items)")


def test_memory_write_lifecycle(server):
    added = _post(server, "/api/memory/add",
                  {"text": "added via api", "kind": "gotcha", "files": ["x.py"]})
    mid = added["id"]
    assert added["ok"]
    _post(server, "/api/memory/update", {"id": mid, "text": "edited via api", "kind": "fact"})
    got = next(m for m in _get(server, "/api/memories") if m["id"] == mid)
    assert got["text"] == "edited via api" and got["kind"] == "fact"
    assert _post(server, "/api/memory/delete", {"id": mid})["ok"]
    assert all(m["id"] != mid for m in _get(server, "/api/memories"))


def test_flush_endpoint(server):
    before = len(_get(server, "/api/codegraph")["nodes"])
    assert before > 0
    r = _post(server, "/api/flush", {})            # scope defaults handled server-side
    assert r["ok"]


def test_bad_route_404(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(server, "/api/nope")
    assert e.value.code == 404
