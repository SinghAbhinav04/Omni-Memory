"""Extraction quality — the guards on what is allowed to BECOME a memory.

Versions through 0.9.x filed raw transcript lines as project facts and gave them file
anchors invented out of version numbers ("0.9", "3.11", "//pypi.org"). Those fake
anchors then satisfied the noise filter's "already names a file" waiver, so the filter
built to stop exactly this never saw them — and the junk went on to draw buildings on
the system map and ride into every session's injected block.

These tests pin the three gates that close that path: anchors must resolve, chat framing
is not content, and a kind must be earned by structure rather than by a word appearing.
"""
from omni_memory import cleanup
from omni_memory import session_memory as sm


# -- 1. an anchor that does not resolve is not an anchor ----------------------

def test_version_numbers_and_hosts_are_not_file_anchors(repo):
    for text in ("shipped 0.9.25 to PyPI", "waiting on the 3.11 run",
                 "see e.g. the note", "published at https://pypi.org/project/x/"):
        assert sm._real_files(repo, text) == [], text


def test_real_paths_resolve(repo):
    assert sm._real_files(repo, "the bug is in svc.py") == ["svc.py"]
    # a path that does not exist in THIS repo is rejected even though it looks real
    assert sm._real_files(repo, "look at src/api/orders.py") == []


def test_anchors_stay_inside_the_repo(repo):
    """An anchor outside the tree is one git cannot hash and the map cannot cite.

    `_FILE_RE` opens with `\\b`, so it cannot begin a match on `..` — a `../x.py`
    mention tokenizes to `x.py` and is then judged on whether *that* resolves in the
    repo. This pins the outcome that matters: nothing outside the tree is ever
    returned, whichever way the candidate was spelled."""
    (repo.parent / "outside.py").write_text("secret = 1\n", encoding="utf-8")
    assert sm._real_files(repo, "compare against ../outside.py") == []
    for anchor in sm._real_files(repo, "svc.py and ../outside.py and /etc/hosts"):
        (repo / anchor).resolve().relative_to(repo.resolve())   # raises if it escaped


def test_extracted_anchors_all_resolve(repo):
    """Whatever the scanner emits, every anchor must exist — this is the invariant the
    rest of the provenance stack (blob shas, staleness, system map) rests on."""
    text = "\n".join([
        "POST /api/orders is handled in svc.py by create_order",
        "assistant: shipped 0.9.25, waiting on the 3.11 run",
    ])
    for item in sm._heuristic_extract(text, repo):
        for f in item["files"]:
            assert (repo / f).is_file(), f


# -- 2. a chat turn is not a project fact -------------------------------------

def test_transcript_role_prefix_is_stripped(repo):
    items = sm._heuristic_extract("assistant: we decided to use Redis for rate limits",
                                  repo)
    assert items and not items[0]["text"].startswith("assistant:")


def test_narration_is_filtered_as_noise():
    for line in ("assistant: **0.9.25 shipped** — pushed to PyPI",
                 "user: this is a strong answer, especially the distinction",
                 "assistant: Let me put the venv somewhere stable",
                 "assistant: Waiting on the 3.11 run (deps reinstalled)"):
        assert cleanup.is_noise(line, source="heuristic"), line


def test_real_facts_survive_the_filter(repo):
    """The filter must not be so tight that genuine memory stops being captured."""
    for line, kind in (
            ("The POST /api/orders endpoint maps to OrderController -> OrderService",
             "endpoint"),
            ("We decided to use a Redis token-bucket for rate limiting", "decision"),
            ("Gotcha: svc.py auto-creates tables on open, so migrations are additive",
             "gotcha")):
        items = sm._heuristic_extract(line, repo)
        kept, _ = cleanup.filter_items(items, source="heuristic")
        assert kept, line
        assert kept[0]["kind"] == kind, (line, kept[0]["kind"])


# -- 3. a kind is earned by structure, not by a word appearing ----------------

def test_endpoint_requires_a_real_method_and_path(repo):
    """`endpoint` maps to `gateway` first in systemmap._ROLE_BY_KIND, so classifying on
    the mere WORD 'endpoint' turned most of the map into gateways."""
    prose = sm._heuristic_extract("now the dashboard API endpoints are wired", repo)
    assert not any(i["kind"] == "endpoint" for i in prose)
    real = sm._heuristic_extract("GET /api/health returns the build sha", repo)
    assert real and real[0]["kind"] == "endpoint"


def test_kind_taxonomy_is_reachable(repo):
    """Only 5 of the 11 kinds were producible, which is why the system map's glossary
    (built from `concept` memories) was permanently empty."""
    text = "\n".join([
        "GET /api/orders lists orders",
        "request -> validator -> repository",
        "CREATE TABLE orders stores one row per checkout",
        "the service publishes to the kafka orders topic",
        "we decided to use Postgres for the ledger",
        "gotcha: the retry wrapper swallows timeouts",
        "TODO: backfill the orders index",
        "an idempotency key is a client-supplied token that dedupes retries",
    ])
    kinds = {i["kind"] for i in sm._heuristic_extract(text, repo)}
    assert {"endpoint", "flow", "db", "event", "decision",
            "gotcha", "todo", "concept"} <= kinds


def test_doc_ingest_does_not_self_certify(store, repo):
    """`ingest_docs` stamps the scanned doc's path onto every line it extracts. If that
    stamp counted as the concrete anchor, each line would certify itself and whole
    paragraphs of a design doc would land in the store."""
    (repo / "DESIGN.md").write_text(
        "# Design\n\nthis section covers the important business logic\n"
        "the handler lives in svc.py and raises ValidationError\n",
        encoding="utf-8")
    sm.ingest_docs(store, repo)
    texts = [m["text"] for m in store.memories(limit=50)]
    assert not any("important business logic" in t for t in texts)
