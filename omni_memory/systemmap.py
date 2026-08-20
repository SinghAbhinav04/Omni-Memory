"""System Map — the repo's implemented architecture as a citation-backed model.

An isometric "city" of runtime roles: buildings for gateways/services/stores/brokers,
routes for the control, data and event paths between them, named end-to-end flows, and
a `path:line-line` citation behind every claim.

What makes this one different from an agent-drawn architecture diagram is the last
column. A generated map asserts its citations once, at snapshot time, on the model's
word — and the moment the code moves it is confidently wrong and says nothing. Every
citation here carries a verdict re-resolved from the git blob sha the memory was
captured against, so the map can show which parts of *itself* have gone stale:

    FRESH        the cited bytes still hash to what the memory was derived from
    DRIFTED      the source changed after capture      -> re-derive the claim
    ORPHANED     the source is gone                    -> drop or re-source
    UNVERIFIABLE no content key, but the source still resolves -> never claimed fresh
    NOT_BINDABLE no addressable source at all          -> reported beside, not inside

A memory with no content key whose declared sources have ALL vanished is ORPHANED, not
UNVERIFIABLE: missing the means to check the bytes is not a reason to go quiet about a
source that plainly is not there.

Like `healthmap.py`, this is a pure projection over data the store already holds — the
code graph plus each memory's anchors and provenance. No new capture, no new schema.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import gitmeta
from .healthmap import _cluster          # same module grouping as the Health Map
from .store import Store

# Runtime role per memory kind, and the zone the building sits in. A cluster's role is
# decided by the strongest role-bearing memory anchored to it (order matters: an
# endpoint on a module makes it a gateway even if it also touches the db).
_ROLE_BY_KIND = [
    ("endpoint", "gateway", "entry"),
    ("event", "broker", "async"),
    ("db", "store", "state"),
    ("component", "service", "application"),
]

# Building silhouette + what the role means, for the legend and explainer panel.
_ROLE_META = {
    "gateway": ("gatehouse", "Parses input and routes control into the system."),
    "service": ("workshop", "Owns business or orchestration logic."),
    "broker": ("depot", "Buffers or distributes messages between units."),
    "store": ("vault", "Persists structured source-of-truth data."),
    "module": ("workshop", "A code unit with no recorded runtime role yet."),
}
_ZONE_BY_ROLE = {"gateway": "entry", "service": "application",
                 "broker": "async", "store": "state", "module": "application"}

# Their spec: 8-18 load-bearing buildings. More source files belong in citations,
# not as more buildings.
_MAX_NODES = 18
_MAX_EDGES = 60

# `A -> B -> C` in an endpoint/flow memory is a causal chain the extractor is asked to
# produce ("ENDPOINT maps (controller->service->repo)"), so it parses into flow steps.
_ARROW = re.compile(r"\s*(?:->|→|=>)\s*")


def _remote_url(root: Path) -> str:
    """The origin remote as an https base, for immutable citation permalinks.
    '' when there's no remote (a local-only repo still gets `path:line-line` text)."""
    url = gitmeta._git(root, "config", "--get", "remote.origin.url")
    if not url:
        return ""
    url = url.strip()
    if url.startswith("git@"):                      # git@github.com:owner/repo.git
        url = "https://" + url[4:].replace(":", "/", 1)
    if url.endswith(".git"):
        url = url[:-4]
    return url if url.startswith("https://") else ""


def _permalink(base: str, commit: str, path: str, lo: int, hi: int) -> str:
    """An immutable forge link pinned to the analyzed commit. Empty when we can't
    build one — the visible `path:line-line` label is always the fallback, so
    evidence stays useful when navigation is sandboxed."""
    if not (base and commit):
        return ""
    from urllib.parse import quote
    return f"{base}/blob/{commit}/{quote(path)}#L{lo}-L{hi}"


# Worst anchor wins, exactly as staleness._integrity_verdict ranks them.
_VERDICT_RANK = {"FRESH": 0, "UNVERIFIABLE": 1, "DRIFTED": 2, "ORPHANED": 3}


def _verdict(recorded: dict, current: dict, declared: list | None = None) -> str:
    """Source-integrity of one memory's anchors, given the shas recorded at capture and
    the shas resolvable right now. Pure function over two dicts — mirrors
    `staleness._integrity_verdict` but reads from a prefetched map so the whole map
    costs one `git hash-object` per unique path instead of one per memory-file pair.

    Kept pure (no git, no store) so a test can drive every verdict without a repo.
    """
    if not recorded:
        # No content key to check against. That is a reason we cannot confirm the bytes
        # — it is NOT permission to stay quiet about a source that is simply gone. A
        # memory whose every declared path fails to resolve today is ORPHANED, and
        # rendering it as benign "legacy" is the confidently-wrong failure this module
        # exists to avoid.
        if declared and not any(p in current for p in declared):
            return "ORPHANED"
        return "UNVERIFIABLE"
    worst = "FRESH"
    for path, sha in recorded.items():
        cur = current.get(path)
        if not cur:
            return "ORPHANED"                       # object missing now → deleted
        if cur != sha and _VERDICT_RANK["DRIFTED"] > _VERDICT_RANK[worst]:
            worst = "DRIFTED"
    return worst


def _worst(verdicts) -> str:
    """The verdict a building inherits from its citations: the worst one it rests on.
    A node is only as trustworthy as its weakest evidence.

    An EMPTY set of verdicts is not FRESH. Nothing was checked, so the honest answer is
    UNVERIFIABLE — defaulting to FRESH would let a building with no resolvable evidence
    render as confirmed, which is the one direction this map must never fail in.
    """
    out = None
    for v in verdicts:
        if out is None or _VERDICT_RANK.get(v, 0) > _VERDICT_RANK.get(out, 0):
            out = v
    return out or "UNVERIFIABLE"


def _sym_index(nodes_raw: list[dict]) -> dict:
    """{(file, symbol name): (line_start, line_end)} so a memory's symbols resolve to
    exact line ranges for its citations."""
    idx = {}
    for n in nodes_raw:
        if n["kind"] in ("function", "method", "class"):
            idx[(n["file"], n["name"])] = (n["line_start"], n["line_end"])
    return idx


def _citations(mem: dict, idx: dict, base: str, commit: str,
               anchors: list | None = None) -> list[dict]:
    """Evidence for one memory: its symbols resolved to line ranges where the code
    graph knows them, else the whole file. `proves` is the memory's own claim — a
    citation should prove the adjacent claim, not merely mention the same noun.

    `anchors` overrides the memory's declared files with the resolved anchor set, so a
    symbol-only memory still cites the file its symbol lives in."""
    out = []
    for f in (anchors if anchors is not None else (mem.get("files") or [])):
        span = next((idx[(f, s)] for s in (mem.get("symbols") or []) if (f, s) in idx), None)
        lo, hi = span if span else (1, 1)
        out.append({"path": f, "startLine": lo, "endLine": hi,
                    "permalink": _permalink(base, commit, f, lo, hi),
                    "proves": mem["text"], "memId": mem["id"]})
    return out


def _role_for(kinds: set) -> str:
    for kind, role, _zone in _ROLE_BY_KIND:
        if kind in kinds:
            return role
    return "module"


def build(store: Store, root: Path, limit: int = _MAX_NODES) -> dict:
    """Project the store into the system-map model (nodes/edges/flows/terms).

    Scoped to the current branch + base like every other retrieval path, so the map
    describes the architecture *you* are working on, not every branch at once.
    """
    from . import branch as branchmod
    cur, base_branch = branchmod.scope(store, root)
    mems = store.memories(branch=None if cur == "*" else cur, base=base_branch,
                          status="active", limit=1_000_000)
    nodes_raw, edges_raw = store.code_graph()
    idx = _sym_index(nodes_raw)
    sym_file = {n["id"]: n["file"] for n in nodes_raw if n["kind"] != "file"}

    commit = gitmeta._git(root, "rev-parse", "HEAD")
    url = _remote_url(root)

    # A memory may anchor by file, by symbol, or both. Symbols resolve to their file via
    # the code graph — without that step a symbol-only memory was placed on no building
    # AND excluded from `not_bindable` (the guard needs BOTH empty), so it vanished from
    # the accounting entirely: neither drawn nor confessed.
    sym_by_name: dict[str, str] = {}
    for n in nodes_raw:
        if n["kind"] != "file":
            sym_by_name.setdefault(n["name"], n["file"])

    def _anchor_files(m: dict) -> list[str]:
        out = list(m.get("files") or [])
        for s in m.get("symbols") or []:
            f = sym_by_name.get(s) or sym_by_name.get(s.split("::")[-1].split(".")[-1])
            if f and f not in out:
                out.append(f)
        return out

    anchors_of = {m["id"]: _anchor_files(m) for m in mems}

    # One git hash per unique cited path — the whole map's provenance cost. Paths a
    # memory declares but that no longer resolve are simply absent from `current`,
    # which is exactly how `_verdict` detects ORPHANED.
    cited_paths = {f for m in mems
                   for f in set(anchors_of[m["id"]]) | set(m.get("files") or [])}
    current = gitmeta.blob_shas(root, sorted(cited_paths))

    # -- roll memories up onto module clusters --------------------------------
    # A building is a code cluster, typed by what's remembered about it — not one
    # building per directory, and not one per memory.
    agg: dict[str, dict] = {}
    not_bindable = 0
    for m in mems:
        anchors = anchors_of[m["id"]]
        if not anchors:
            not_bindable += 1                       # no addressable source: beside, not inside
            continue
        for f in anchors:
            a = agg.setdefault(_cluster(f), {"kinds": set(), "mems": [], "files": set(),
                                             "seen": set()})
            a["kinds"].add(m["kind"])
            a["files"].add(f)
            if m["id"] not in a["seen"]:            # a memory spanning 2 files in one
                a["seen"].add(m["id"])              # cluster must not count twice
                a["mems"].append(m)

    # clusters the code graph knows about but nothing is remembered about → blind spots.
    # They are load-bearing information: the un-mapped regions of the system.
    sym_count: dict[str, int] = {}
    for n in nodes_raw:
        if n["kind"] != "file":
            sym_count[_cluster(n["file"])] = sym_count.get(_cluster(n["file"]), 0) + 1
    for c in sym_count:
        agg.setdefault(c, {"kinds": set(), "mems": [], "files": set(), "seen": set()})

    nodes = []
    for name, a in agg.items():
        role = _role_for(a["kinds"])
        cites, verdicts = [], []
        for m in a["mems"]:
            cs = _citations(m, idx, url, commit, anchors_of[m["id"]])
            cites.extend(cs)
            if cs:
                verdicts.append(_verdict(m.get("blob_shas") or {}, current,
                                         anchors_of[m["id"]]))
        blind = not a["mems"]
        building, plain = _ROLE_META[role]
        nodes.append({
            "id": name, "label": name, "kind": role, "building": building,
            "zone": _ZONE_BY_ROLE[role], "height": sym_count.get(name, 1),
            "plainRole": plain,
            "repoRole": (a["mems"][0]["text"] if a["mems"] else
                         "No memory anchored here yet — a blind spot on the map."),
            "verdict": "BLIND" if blind else _worst(verdicts),
            # `confidence` per their spec: observed | inferred | unknown. Ours is earned,
            # not asserted — `observed` requires the top evidence tier AND fresh sources.
            "confidence": ("unknown" if blind else
                           "observed" if any(m.get("evidence") == "verified" for m in a["mems"])
                           and _worst(verdicts) == "FRESH" else "inferred"),
            "memCount": len(a["mems"]),
            "stale": sum(1 for m in a["mems"] if m.get("stale")),
            "citations": cites[:8]})

    # keep the load-bearing city: remembered clusters first, then the largest blind ones
    nodes.sort(key=lambda n: (n["memCount"] > 0, n["memCount"], n["height"]), reverse=True)
    nodes = nodes[:limit]
    keep = {n["id"] for n in nodes}

    # -- routes ---------------------------------------------------------------
    # Call edges rolled up to cluster level. Kind is decided by what the destination
    # IS: a route into a store carries data, one into a broker carries events.
    kind_by_node = {n["id"]: n["kind"] for n in nodes}
    ew: dict[tuple, int] = {}
    for e in edges_raw:
        if e.get("rel") != "calls":
            continue
        sf, df = sym_file.get(e["src"]), sym_file.get(e["dst"])
        if not (sf and df):
            continue
        sc, dc = _cluster(sf), _cluster(df)
        if sc != dc and sc in keep and dc in keep:
            ew[(sc, dc)] = ew.get((sc, dc), 0) + 1
    _EDGE_KIND = {"store": ("data", "reads/writes"), "broker": ("event", "publishes"),
                  "gateway": ("control", "routes"), "service": ("control", "calls"),
                  "module": ("control", "calls")}
    edges = []
    for (s, d), w in sorted(ew.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_EDGES]:
        ekind, verb = _EDGE_KIND[kind_by_node[d]]
        edges.append({"id": f"{s}->{d}", "from": s, "to": d, "kind": ekind,
                      "verb": verb, "weight": w,
                      "explanation": f"{s} {verb} {d} ({w} call site{'s' if w > 1 else ''} "
                                     f"in the code graph).",
                      "confidence": "observed", "citations": []})

    # -- flows ----------------------------------------------------------------
    # `A -> B -> C` in a flow/endpoint memory is already a causal chain (the extractor
    # is told to capture endpoint maps that way), so it parses straight into steps.
    flows = []
    for m in mems:
        if m["kind"] not in ("flow", "endpoint"):
            continue
        parts = [p.strip() for p in _ARROW.split(m["text"]) if p.strip()]
        if len(parts) < 2:
            continue
        anchors = anchors_of[m["id"]]
        home = next((_cluster(f) for f in anchors if _cluster(f) in keep), None)
        cite = (_citations(m, idx, url, commit, anchors) or [None])[0]
        flows.append({
            "id": m["id"], "label": parts[0][:60], "plainSummary": m["text"],
            "verdict": _verdict(m.get("blob_shas") or {}, current, anchors)
                       if anchors else "NOT_BINDABLE",
            # one citation resolve per flow, not one per step — the steps of a single
            # memory all rest on the same evidence
            "steps": [{"nodeId": home, "action": p, "citation": cite} for p in parts]})

    # -- glossary -------------------------------------------------------------
    # Their spec wants every technical term defined. `concept` memories already ARE
    # that: one plain sentence about a domain term, captured from real sessions.
    terms = [{"name": (m["text"].split(" is ")[0][:40] if " is " in m["text"]
                       else m["text"][:40]),
              "plainMeaning": m["text"],
              "inThisRepo": ", ".join(anchors_of[m["id"]]) or "—",
              "citation": (_citations(m, idx, url, commit,
                                      anchors_of[m["id"]]) or [None])[0]}
             for m in mems if m["kind"] == "concept"][:12]

    # Slice BEFORE counting: the header and the CLI report `stats.flows`, and a count
    # larger than the picker offers promises flows the reader can never reach.
    flows = flows[:8]

    counts: dict[str, int] = {}
    for n in nodes:
        counts[n["verdict"]] = counts.get(n["verdict"], 0) + 1
    return {
        "repository": {"url": url, "branch": cur, "base": base_branch,
                       "commit": commit, "commitShort": commit[:7],
                       "analyzedAt": time.time()},
        "nodes": nodes, "edges": edges, "flows": flows, "terms": terms,
        "stats": {"buildings": len(nodes), "routes": len(edges), "flows": len(flows),
                  "memories": len(mems), "notBindable": not_bindable,
                  "verdicts": counts},
    }


def to_json(model: dict) -> str:
    """Serialize for inline embedding. `<` is escaped so repository-derived text can
    never close the host <script> element (their trust-boundary rule, and ours: memory
    text is teammate-controlled via committed team shards)."""
    return json.dumps(model, default=str).replace("<", "\\u003c")


# -- standalone artifact -------------------------------------------------------
_STATIC = Path(__file__).parent / "static" / "index.html"


def _slice(text: str, start: str, end: str) -> str:
    """The region strictly between two sentinel markers in the dashboard source.

    Slices from the END of the start marker's LINE, so any prose sharing that line stays
    behind — a leaked comment tail is a syntax error in the extracted output, and it is
    exactly the kind of break that would only show up in the artifact, never the app.
    """
    if start not in text or end not in text:
        raise RuntimeError(
            f"marker {start!r}/{end!r} not found in {_STATIC.name} — the standalone "
            "artifact shares the dashboard's renderer via these sentinels; restore them.")
    after = text.split(start, 1)[1]
    return after[after.index("\n") + 1:].split(end, 1)[0] if "\n" in after else ""


# The shell supplies the handful of globals the extracted renderer expects from the
# dashboard. `api` returns the INLINED model instead of fetching: the artifact must keep
# working after the repo it describes is gone, so it makes no runtime request at all.
_SHELL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>System Map — %(title)s</title>
<style>
  :root{--bg:#f2e8d5;--panel:#fbf5e9;--panel-2:#eddfc7;--border:#e4d5b8;--border-2:#d6c29c;
    --fg:#3a2c18;--fg-2:#7c6442;--fg-3:#a48c66;--accent:#b5791b;--accent-2:#8a5412;
    --code-bg:#f0e5d0;--shadow:rgba(96,64,18,.16);
    --ui:"Segoe UI",system-ui,-apple-system,Roboto,Helvetica,Arial,sans-serif;
    --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  :root[data-theme="dark"]{--bg:#1b1509;--panel:#241d0f;--panel-2:#2f2615;--border:#3c3018;
    --border-2:#4d3e22;--fg:#eaddc2;--fg-2:#b39a6f;--fg-3:#8a7551;--accent:#d9a441;
    --accent-2:#e8bd57;--code-bg:#2a2213;--shadow:rgba(0,0,0,.55)}
  @media (prefers-color-scheme:dark){:root:not([data-theme]){--bg:#1b1509;--panel:#241d0f;
    --panel-2:#2f2615;--border:#3c3018;--border-2:#4d3e22;--fg:#eaddc2;--fg-2:#b39a6f;
    --fg-3:#8a7551;--accent:#d9a441;--accent-2:#e8bd57;--code-bg:#2a2213;--shadow:rgba(0,0,0,.55)}}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%%;background:var(--bg);color:var(--fg);font:13.5px/1.55 var(--ui)}
  #hdr{padding:13px 22px;border-bottom:1px solid var(--border);background:var(--panel);
    color:var(--fg-2);font-size:12.5px;display:flex;gap:14px;align-items:center}
  #hdr b{color:var(--fg)}
  #themebtn{margin-left:auto;background:var(--panel-2);border:1px solid var(--border-2);
    color:var(--fg);border-radius:7px;padding:4px 10px;font:inherit;font-size:12px;cursor:pointer}
  svg{width:100%%;display:block}
  .hint{color:var(--fg-3)}
  .chip{display:inline-block;border:1px solid var(--border-2);border-radius:9px;padding:1px 8px;
    margin-right:6px;color:var(--fg-2);font-size:10px;font-family:var(--mono)}
  .sec{color:var(--fg-3);font-size:10px;text-transform:uppercase;letter-spacing:.08em;margin:14px 0 5px}
  #insp{position:fixed;top:0;right:0;width:390px;max-width:92vw;height:100vh;background:var(--panel);
    border-left:1px solid var(--border);overflow:auto;transform:translateX(102%%);
    transition:transform .18s;z-index:9;box-shadow:-2px 0 14px var(--shadow)}
  #insp.open{transform:translateX(0)}
  #insp .ipanel{padding:16px 18px 40px}
  #insp .ihead{display:flex;justify-content:space-between;align-items:center;
    border-bottom:1px solid var(--border);padding-bottom:9px;margin-bottom:10px;font-size:15px}
  #insp .iclose{cursor:pointer;color:var(--fg-2);font-size:16px}
  #insp .irow{display:flex;align-items:center}
  @media (prefers-reduced-motion:reduce){#insp{transition:none}}
%(css)s
</style></head><body>
<div id="hdr"><b>System Map</b><span id="meta"></span>
  <button id="themebtn" type="button">&#9689; Theme</button></div>
<div id="view"></div><aside id="insp"></aside>
<script id="sm-model" type="application/json">%(model)s</script>
<script>
// ── shims: the extracted renderer expects these from the dashboard ──
const SM_MODEL = JSON.parse(document.getElementById('sm-model').textContent);
const view = document.getElementById('view'), hdr = document.getElementById('meta');
const insp = document.getElementById('insp');
let activeTab = 'system';
function esc(s){ return (''+s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function isDark(){ const t=document.documentElement.getAttribute('data-theme');
  return t ? t==='dark' : matchMedia('(prefers-color-scheme:dark)').matches; }
function closeInspector(){ insp.classList.remove('open'); }
// No network: the model is inlined, so the artifact still opens long after the analyzed
// checkout is gone — and it cannot leak what it describes to anywhere.
async function api(){ return SM_MODEL; }
document.getElementById('themebtn').onclick=()=>{
  document.documentElement.setAttribute('data-theme', isDark()?'light':'dark');
  renderSystem();
};
%(js)s
renderSystem();
</script></body></html>
"""


def artifact(store: Store, root: Path, limit: int = _MAX_NODES) -> str:
    """Render the standalone, self-contained HTML map.

    The CSS and the renderer are lifted verbatim out of the dashboard's `index.html`
    between sentinel markers, so there is exactly one implementation of the map and the
    shareable file can never drift from what the dashboard shows.
    """
    model = build(store, root, limit=limit)
    src = _STATIC.read_text(encoding="utf-8")
    name = root.name or "repository"
    return _SHELL % {
        "title": name.replace("<", "").replace("&", "+"),
        "css": _slice(src, "/* SM-CSS:START */", "/* SM-CSS:END */"),
        "js": _slice(src, "// SM-JS:START", "// SM-JS:END"),
        "model": to_json(model),
    }
