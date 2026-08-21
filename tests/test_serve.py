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


# ── /api/integrity: the trust story, which until now only existed in a terminal ──

def test_integrity_endpoint_reports_every_block(server):
    """The dashboard's provenance panel is backed by the same measurements `doctor`
    prints. All three blocks must be present even on a fresh store — a missing block is
    indistinguishable from a passing one once it is rendered."""
    d = _get(server, "/api/integrity")
    assert set(d) == {"provenance", "collector", "identifier"}
    assert d["collector"]["verdict"] in ("OK", "FAIL", "SKIP")
    assert d["identifier"]["declared"].startswith("uuid4()")


def test_integrity_reports_the_cliff_for_every_population(server):
    """The cliff is reported unconditionally, for both key populations, with the reason
    it is silent when it is — a warning that cannot fire and a warning that found nothing
    render identically otherwise."""
    pops = {p["population"]: p for p in _get(server, "/api/integrity")["identifier"]["populations"]}
    assert "memory.id" in pops and "code_nodes.id" in pops
    for p in pops.values():
        assert "collides_at_length" in p and "reason_empty" in p
        # The curve travels with the cliff wherever a curve exists at all. One key has
        # no curve and no cliff, and reporting an empty one is the honest answer.
        assert bool(p["loss_curve"]) == (p["keys"] >= 2)
    assert pops["code_nodes.id"]["fold"] is None      # nothing truncates it
    assert pops["code_nodes.id"]["distance_to_cliff"] is None


def test_one_failing_probe_does_not_blank_the_others(server, monkeypatch):
    """Each block is guarded on its own. A crash in the identifier measurement must not
    take down the panel that would have explained it."""
    from omni_memory import identifier
    monkeypatch.setattr(identifier, "identifier_contract",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    d = _get(server, "/api/integrity")
    assert d["identifier"] == {"error": "boom"}
    assert "verdict" in d["collector"]                # the others still measured
