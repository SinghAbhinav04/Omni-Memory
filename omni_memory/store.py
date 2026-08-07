"""Local SQLite store for OmniMemory — memories, branches, commits, flows.

Everything lives under `<project>/.omni-memory/omni.db`. Local-first, no cloud.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

STORE_DIRNAME = ".omni-memory"


def find_project_root(start: Optional[Path] = None) -> Path:
    """Nearest ancestor with a .git (or .omni-memory); else cwd."""
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists() or (cand / STORE_DIRNAME).exists():
            return cand
    return p


def store_dir(root: Optional[Path] = None) -> Path:
    d = find_project_root(root) / STORE_DIRNAME
    d.mkdir(exist_ok=True)
    # Self-ignore: a `*` .gitignore inside the store makes the whole directory
    # (the SQLite db + its -wal/-shm sidecars) invisible to git in any project,
    # so users never see `.omni-memory/` clutter and never commit the live db.
    gi = d / ".gitignore"
    if not gi.exists():
        try:
            gi.write_text("*\n")
        except Exception:  # noqa: BLE001
            pass
    return d


# Kinds modeled on a real, battle-tested AI knowledge base (work/docs):
# architecture/decisions, domain concepts, execution flows, event contracts
# (kafka), endpoint maps (controller→service→repo+params), db schema, reusable
# components, gotchas/known-issues, unverified assumptions, todos, plain facts.
KINDS = ("decision", "concept", "flow", "event", "endpoint", "db",
         "component", "gotcha", "assumption", "todo", "fact")
STATUSES = ("active", "merged", "abandoned", "superseded")


@dataclass
class Memory:
    text: str
    kind: str = "fact"
    branch: str = "main"
    files: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    commit_range: str = ""
    status: str = "active"
    confidence: float = 0.8
    source: str = "session"
    id: str = ""
    created: float = 0.0
    updated: float = 0.0
    supersedes_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        return d


SCHEMA = """
CREATE TABLE IF NOT EXISTS memory(
  id TEXT PRIMARY KEY, branch TEXT, kind TEXT, text TEXT,
  files TEXT, symbols TEXT, commit_range TEXT,
  created REAL, updated REAL, status TEXT, confidence REAL,
  source TEXT, supersedes_id TEXT,
  stale INTEGER DEFAULT 0, stale_since REAL, stale_files TEXT);
CREATE TABLE IF NOT EXISTS branches(
  name TEXT PRIMARY KEY, creator TEXT, created_at REAL, base_branch TEXT,
  ahead INTEGER, behind INTEGER, status TEXT, merged_at REAL,
  merge_commit TEXT, into_branch TEXT);
CREATE TABLE IF NOT EXISTS commits(
  sha TEXT PRIMARY KEY, branch TEXT, author TEXT, date REAL,
  message TEXT, files TEXT);
CREATE TABLE IF NOT EXISTS flows(
  id TEXT PRIMARY KEY, name TEXT, entry TEXT, steps TEXT, branch TEXT);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS code_nodes(
  id TEXT PRIMARY KEY, kind TEXT, name TEXT, file TEXT,
  line_start INTEGER, line_end INTEGER, parent TEXT,
  signature TEXT, doc TEXT, raises TEXT, calls TEXT);
CREATE TABLE IF NOT EXISTS code_edges(src TEXT, dst TEXT, rel TEXT);
CREATE INDEX IF NOT EXISTS idx_mem_branch ON memory(branch);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memory(status);
CREATE INDEX IF NOT EXISTS idx_cedge_dst ON code_edges(dst);
CREATE INDEX IF NOT EXISTS idx_cnode_file ON code_nodes(file);
CREATE INDEX IF NOT EXISTS idx_cnode_name ON code_nodes(name);
"""


def global_dir() -> Path:
    """The user-level store, shared across every project (~/.omni-memory)."""
    d = Path.home() / STORE_DIRNAME
    d.mkdir(exist_ok=True)
    gi = d / ".gitignore"
    if not gi.exists():
        try:
            gi.write_text("*\n")
        except Exception:  # noqa: BLE001
            pass
    return d


class Store:
    def __init__(self, root: Optional[Path] = None, exact_dir: Optional[Path] = None):
        # exact_dir bypasses project-root detection (used for the global store,
        # so it can't be accidentally captured by a git repo above $HOME).
        if exact_dir is not None:
            self.dir = Path(exact_dir)
            self.dir.mkdir(parents=True, exist_ok=True)
            gi = self.dir / ".gitignore"
            if not gi.exists():
                try:
                    gi.write_text("*\n")
                except Exception:  # noqa: BLE001
                    pass
        else:
            self.dir = store_dir(root)
        # check_same_thread=False: the dashboard server handles requests on
        # worker threads. WAL + a busy timeout let the background auto-refresh
        # watcher rebuild the code graph while HTTP requests read/write on their
        # own connections without hitting "database is locked" (was surfacing as
        # intermittent 500s / a stale dashboard).
        self.db = sqlite3.connect(self.dir / "omni.db",
                                  check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first schema (SQLite lacks IF NOT
        EXISTS on ADD COLUMN, so check PRAGMA and add what's missing)."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(memory)")}
        for col, decl in (("stale", "INTEGER DEFAULT 0"),
                          ("stale_since", "REAL"), ("stale_files", "TEXT"),
                          ("uses", "INTEGER DEFAULT 0"), ("last_used", "REAL"),
                          ("quarantined_at", "REAL"), ("quarantine_reason", "TEXT")):
            if col not in have:
                self.db.execute(f"ALTER TABLE memory ADD COLUMN {col} {decl}")
        cn = {r["name"] for r in self.db.execute("PRAGMA table_info(code_nodes)")}
        for col in ("signature", "doc", "raises", "calls"):
            if col not in cn:
                self.db.execute(f"ALTER TABLE code_nodes ADD COLUMN {col} TEXT")

    # -- meta / toggles -----------------------------------------------------
    def get_meta(self, key: str, default: Any = None) -> Any:
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
                        (key, json.dumps(value)))
        self.db.commit()

    # -- memories -----------------------------------------------------------
    def add_memory(self, m: Memory) -> Memory:
        m.id = m.id or uuid.uuid4().hex[:12]
        m.created = m.created or time.time()
        m.updated = time.time()
        # contradiction handling: same branch+kind and near-identical text supersedes
        self._supersede_duplicates(m)
        self.db.execute(
            "INSERT OR REPLACE INTO memory"
            "(id,branch,kind,text,files,symbols,commit_range,created,updated,"
            "status,confidence,source,supersedes_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m.id, m.branch, m.kind, m.text, json.dumps(m.files),
             json.dumps(m.symbols), m.commit_range, m.created, m.updated,
             m.status, m.confidence, m.source, m.supersedes_id))
        self.db.commit()
        return m

    def _supersede_duplicates(self, m: Memory) -> None:
        key = _norm(m.text)
        rows = self.db.execute(
            "SELECT id,text FROM memory WHERE branch=? AND kind=? AND status='active'",
            (m.branch, m.kind)).fetchall()
        for r in rows:
            if _similar(key, _norm(r["text"])):
                self.db.execute("UPDATE memory SET status='superseded',updated=? WHERE id=?",
                                (time.time(), r["id"]))
                m.supersedes_id = r["id"]

    def bump_uses(self, ids: list[str]) -> int:
        """Record that these memories were cited by the agent (relevance signal)."""
        if not ids:
            return 0
        now = time.time()
        cur = self.db.executemany(
            "UPDATE memory SET uses=COALESCE(uses,0)+1, last_used=? WHERE id=?",
            [(now, i) for i in ids])
        self.db.commit()
        return cur.rowcount

    def memories(self, branch: Optional[str] = None, base: Optional[str] = None,
                 kinds: Optional[list[str]] = None, files: Optional[list[str]] = None,
                 status: str = "active", query: str = "", limit: int = 200,
                 context: Optional[dict] = None) -> list[dict]:
        # Scope in SQL (branch/status/kind); rank relevance in Python. We no
        # longer filter by `LIKE` per token — that required *every* term to be
        # present and then sorted by recency. Instead fetch the scoped candidate
        # set and let rank.py score it (IDF tiers + coverage + file/recency).
        sql = "SELECT * FROM memory WHERE 1=1"
        args: list[Any] = []
        if status:
            sql += " AND status=?"
            args.append(status)
        branches = [b for b in (branch, base) if b]
        if branches:
            sql += f" AND branch IN ({','.join('?' * len(branches))})"
            args += branches
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += kinds
        sql += " ORDER BY updated DESC"
        rows = [self._row_to_mem(r) for r in self.db.execute(sql, args).fetchall()]
        if query or files or context:
            from . import rank as _rank
            rows = _rank.rank(rows, query, files=files, context=context)
        return rows[:limit]

    def set_stale(self, mem_id: str, stale: bool, since: Optional[float],
                  files: Optional[list[str]]) -> None:
        if since is None:  # keep the existing stale_since, just update flag/files
            self.db.execute(
                "UPDATE memory SET stale=?, stale_files=? WHERE id=?",
                (1 if stale else 0, json.dumps(files or []), mem_id))
        else:
            self.db.execute(
                "UPDATE memory SET stale=?, stale_since=?, stale_files=? WHERE id=?",
                (1 if stale else 0, since, json.dumps(files or []), mem_id))

    # -- lifecycle / eviction ----------------------------------------------
    def update_branch_status(self, name: str, status: str) -> None:
        self.db.execute("UPDATE branches SET status=? WHERE name=?", (status, name))
        self.db.commit()

    def reanchor_memory(self, mem_id: str, commit_range: str = "") -> bool:
        """Mark a stale memory re-verified against the current code: clear the
        stale flag and re-anchor it to the current commit so staleness is
        measured from here on."""
        cur = self.db.execute(
            "UPDATE memory SET stale=0, stale_since=NULL, stale_files=NULL, "
            "commit_range=?, updated=? WHERE id=?",
            (commit_range, time.time(), mem_id))
        self.db.commit()
        return cur.rowcount > 0

    def reanchor_branch(self, frm: str, to: str) -> int:
        """Re-tag a merged branch's active memories onto the base branch."""
        cur = self.db.execute(
            "UPDATE memory SET branch=? WHERE branch=? AND status='active'", (to, frm))
        self.db.commit()
        return cur.rowcount

    def quarantine(self, mem_id: str, reason: str) -> None:
        self.db.execute(
            "UPDATE memory SET status='abandoned', quarantined_at=?, "
            "quarantine_reason=? WHERE id=?", (time.time(), reason, mem_id))
        self.db.commit()

    def restore(self, mem_id: str) -> int:
        cur = self.db.execute(
            "UPDATE memory SET status='active', quarantined_at=NULL, "
            "quarantine_reason=NULL WHERE id=? AND status='abandoned'", (mem_id,))
        self.db.commit()
        return cur.rowcount

    def restore_branch(self, branch: str) -> int:
        cur = self.db.execute(
            "UPDATE memory SET status='active', quarantined_at=NULL, "
            "quarantine_reason=NULL WHERE branch=? AND status='abandoned'", (branch,))
        self.db.commit()
        return cur.rowcount

    def purgeable(self, before: float) -> list[dict]:
        """Long-quarantined, never-cited memories eligible for hard deletion."""
        rows = self.db.execute(
            "SELECT id, kind, branch, text, quarantined_at FROM memory "
            "WHERE status='abandoned' AND quarantined_at IS NOT NULL "
            "AND quarantined_at < ? AND COALESCE(uses,0)=0", (before,)).fetchall()
        return [dict(r) for r in rows]

    def delete(self, ids: list[str]) -> int:
        if not ids:
            return 0
        cur = self.db.executemany("DELETE FROM memory WHERE id=?", [(i,) for i in ids])
        self.db.commit()
        return cur.rowcount

    def forget(self, mem_id: str) -> bool:
        cur = self.db.execute("UPDATE memory SET status='abandoned',updated=? WHERE id=?",
                              (time.time(), mem_id))
        self.db.commit()
        return cur.rowcount > 0

    def get_memory(self, mem_id: str) -> Optional[dict]:
        r = self.db.execute("SELECT * FROM memory WHERE id=?", (mem_id,)).fetchone()
        return self._row_to_mem(r) if r else None

    def export_memories(self, status: str = "active") -> dict:
        """A portable snapshot of memories — write it to a file to move/share a
        project's memory across folders, machines, IDEs, or a team (commit it)."""
        return {"omni_memory_export": 1, "exported_at": time.time(),
                "memories": self.memories(status=status, limit=100000)}

    def import_memories(self, data: dict, source: str = "imported") -> int:
        """Load exported memories into this store. Existing ids are skipped, so
        re-importing is idempotent and merging two exports is safe. Returns the
        number newly added."""
        added = 0
        for m in data.get("memories", []):
            mid = m.get("id") or uuid.uuid4().hex[:12]
            if self.db.execute("SELECT 1 FROM memory WHERE id=?", (mid,)).fetchone():
                continue
            now = time.time()
            self.db.execute(
                "INSERT INTO memory(id,branch,kind,text,files,symbols,commit_range,"
                "created,updated,status,confidence,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, m.get("branch", "main"), m.get("kind", "fact"), m.get("text", ""),
                 json.dumps(m.get("files", [])), json.dumps(m.get("symbols", [])),
                 m.get("commit_range", ""), m.get("created", now), now,
                 "active", m.get("confidence", 0.8), source))
            added += 1
        self.db.commit()
        return added

    def overview(self) -> dict:
        """A single aggregate for the dashboard's Overview tab: memory counts by
        status/kind, code-graph size, branch health, and the most-cited memories."""
        counts = self.counts()
        stale = self.db.execute(
            "SELECT COUNT(*) n FROM memory WHERE status='active' AND stale=1").fetchone()["n"]
        kinds = {r["kind"]: r["c"] for r in self.db.execute(
            "SELECT kind, COUNT(*) c FROM memory WHERE status='active' "
            "GROUP BY kind ORDER BY c DESC")}
        nodes, edges = self.code_graph()
        files = sum(1 for n in nodes if n["kind"] == "file")
        br = {r["status"] or "active": r["c"] for r in self.db.execute(
            "SELECT status, COUNT(*) c FROM branches GROUP BY status")}
        top = [dict(r) for r in self.db.execute(
            "SELECT id, text, kind, uses FROM memory WHERE status='active' AND uses>0 "
            "ORDER BY uses DESC LIMIT 5")]
        return {
            "memories": {"active": counts.get("active", 0), "stale": stale,
                         "merged": counts.get("merged", 0),
                         "superseded": counts.get("superseded", 0)},
            "kinds": kinds,
            "code": {"symbols": len(nodes) - files, "edges": len(edges), "files": files},
            "branches": {"total": sum(br.values()), "by_status": br},
            "top_cited": top,
        }

    def duplicate_groups(self) -> list:
        """Cluster active memories that say near-identical things (same branch,
        token-overlap over threshold) so the dashboard can offer to merge them.
        Returns a list of groups, each a list of memory dicts (newest first)."""
        rows = [self._row_to_mem(r) for r in self.db.execute(
            "SELECT * FROM memory WHERE status='active' ORDER BY updated DESC")]
        groups, used = [], set()
        for i, a in enumerate(rows):
            if a["id"] in used:
                continue
            na = _norm(a["text"])
            group = [a]
            for b in rows[i + 1:]:
                if b["id"] in used or b["branch"] != a["branch"]:
                    continue
                if _similar(na, _norm(b["text"])):
                    group.append(b)
                    used.add(b["id"])
            if len(group) > 1:
                used.add(a["id"])
                groups.append(group)
        return groups

    def merge_memories(self, keep_id: str, drop_ids: list) -> int:
        """Merge duplicates: fold the dropped memories' files into the kept one,
        then archive the drops. Returns how many were archived."""
        keep = self.get_memory(keep_id)
        if not keep:
            return 0
        files = list(keep.get("files") or [])
        for did in drop_ids:
            d = self.get_memory(did)
            if d:
                for f in (d.get("files") or []):
                    if f not in files:
                        files.append(f)
        self.update_memory(keep_id, files=files)
        n = 0
        for did in drop_ids:
            if did != keep_id and self.forget(did):
                n += 1
        return n

    def search(self, query: str, limit: int = 25) -> dict:
        """One free-text search across memories and code symbols, for the
        dashboard's global search. Returns {'memories': [...], 'symbols': [...]}."""
        q = (query or "").strip()
        if not q:
            return {"memories": [], "symbols": []}
        like = f"%{q}%"
        mems = [self._row_to_mem(r) for r in self.db.execute(
            "SELECT * FROM memory WHERE status='active' AND text LIKE ? "
            "ORDER BY updated DESC LIMIT ?", (like, limit)).fetchall()]
        syms = [dict(r) for r in self.db.execute(
            "SELECT id,name,kind,file,line_start,signature,doc FROM code_nodes "
            "WHERE kind!='file' AND (name LIKE ? OR signature LIKE ? OR doc LIKE ?) "
            "ORDER BY (name LIKE ?) DESC, name LIMIT ?",
            (like, like, like, like, limit)).fetchall()]
        return {"memories": mems, "symbols": syms}

    def update_memory(self, mem_id: str, text: Optional[str] = None,
                      kind: Optional[str] = None,
                      files: Optional[list] = None) -> bool:
        """Edit a memory in place (dashboard editing). Only provided fields change."""
        sets, vals = [], []
        if text is not None:
            sets.append("text=?"); vals.append(text)
        if kind is not None:
            sets.append("kind=?"); vals.append(kind)
        if files is not None:
            sets.append("files=?"); vals.append(json.dumps(files))
        if not sets:
            return False
        sets.append("updated=?"); vals.append(time.time())
        vals.append(mem_id)
        cur = self.db.execute(
            f"UPDATE memory SET {','.join(sets)} WHERE id=?", vals)
        self.db.commit()
        return cur.rowcount > 0

    def counts(self) -> dict[str, int]:
        rows = self.db.execute(
            "SELECT status, COUNT(*) c FROM memory GROUP BY status").fetchall()
        return {r["status"]: r["c"] for r in rows}

    @staticmethod
    def _row_to_mem(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["files"] = json.loads(d["files"] or "[]")
        d["symbols"] = json.loads(d["symbols"] or "[]")
        d["stale"] = bool(d.get("stale"))
        d["stale_files"] = json.loads(d.get("stale_files") or "[]")
        d["uses"] = d.get("uses") or 0
        return d

    # -- code graph ---------------------------------------------------------
    _CODE_COLS = ("id", "kind", "name", "file", "line_start", "line_end",
                  "parent", "signature", "doc", "raises", "calls")

    def replace_code_graph(self, nodes: list[dict], edges: list[dict]) -> None:
        self.db.execute("DELETE FROM code_nodes")
        self.db.execute("DELETE FROM code_edges")
        cols = self._CODE_COLS
        ph = ",".join("?" * len(cols))
        self.db.executemany(
            f"INSERT OR REPLACE INTO code_nodes({','.join(cols)}) VALUES({ph})",
            [(n["id"], n["kind"], n["name"], n["file"], n["line_start"],
              n["line_end"], n.get("parent"), n.get("signature", ""),
              n.get("doc", ""), json.dumps(n.get("raises", []) or []),
              json.dumps(n.get("calls", []) or [])) for n in nodes])
        self.db.executemany("INSERT INTO code_edges VALUES(?,?,?)",
                            [(e["src"], e["dst"], e["rel"]) for e in edges])
        self.db.commit()

    def code_graph(self) -> tuple[list[dict], list[dict]]:
        nodes = [dict(r) for r in self.db.execute("SELECT * FROM code_nodes")]
        edges = [dict(r) for r in self.db.execute("SELECT * FROM code_edges")]
        return nodes, edges

    def symbol_dossier(self, sid: str) -> Optional[dict]:
        """Everything the node-detail inspector shows for one code symbol:
        its signature/doc/raises, the symbols it calls (downstream, resolved +
        clickable) and the symbols that call it (upstream), plus raw call names
        (incl. external ones like emits/publishes that don't resolve in-repo)."""
        row = self.db.execute("SELECT * FROM code_nodes WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        n = dict(row)
        for k in ("raises", "calls"):
            try:
                n[k] = json.loads(n.get(k) or "[]")
            except Exception:  # noqa: BLE001
                n[k] = []

        def _mini(ids):
            if not ids:
                return []
            q = ",".join("?" * len(ids))
            rows = self.db.execute(
                f"SELECT id,name,kind,file,line_start,signature FROM code_nodes "
                f"WHERE id IN ({q})", list(ids)).fetchall()
            return [dict(r) for r in rows]

        down = [r["dst"] for r in self.db.execute(
            "SELECT dst FROM code_edges WHERE src=? AND rel='calls'", (sid,))]
        up = [r["src"] for r in self.db.execute(
            "SELECT src FROM code_edges WHERE dst=? AND rel='calls'", (sid,))]
        bases = [r["dst"] for r in self.db.execute(
            "SELECT dst FROM code_edges WHERE src=? AND rel='inherits'", (sid,))]
        children = [r["dst"] for r in self.db.execute(
            "SELECT dst FROM code_edges WHERE src=? AND rel='contains'", (sid,))]
        n["calls_out"] = _mini(down)
        n["called_by"] = _mini(up)
        n["inherits"] = _mini(bases)
        n["contains"] = _mini(children)
        return n

    def flush(self, scope: str = "all") -> dict:
        """Wipe stored data so it can be rebuilt from scratch. Settings in `meta`
        (enabled, branch_aware, default_branch) are preserved. Returns row counts
        deleted per table.

          all    → memory + flows + branches + commits + code graph
          memory → memory + flows only
          graph  → code graph only (code_nodes/code_edges)
        """
        groups = {
            "memory": ["memory", "flows"],
            "graph": ["code_nodes", "code_edges"],
            "all": ["memory", "flows", "branches", "commits",
                    "code_nodes", "code_edges"],
        }
        tables = groups.get(scope, groups["all"])
        counts = {}
        for t in tables:
            counts[t] = self.db.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            self.db.execute(f"DELETE FROM {t}")
        self.db.commit()
        return counts

    def has_code_graph(self) -> bool:
        return bool(self.db.execute("SELECT 1 FROM code_nodes LIMIT 1").fetchone())

    # -- branches / commits -------------------------------------------------
    def upsert_branch(self, **kw: Any) -> None:
        cols = ("name", "creator", "created_at", "base_branch", "ahead", "behind",
                "status", "merged_at", "merge_commit", "into_branch")
        vals = [kw.get(c) for c in cols]
        self.db.execute(
            f"INSERT OR REPLACE INTO branches({','.join(cols)}) "
            f"VALUES({','.join('?' * len(cols))})", vals)
        self.db.commit()

    def branches(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM branches ORDER BY created_at").fetchall()]

    def upsert_commit(self, sha: str, branch: str, author: str, date: float,
                      message: str, files: list[str]) -> None:
        self.db.execute("INSERT OR REPLACE INTO commits VALUES(?,?,?,?,?,?)",
                        (sha, branch, author, date, message, json.dumps(files)))
        self.db.commit()

    def commits(self, branch: Optional[str] = None) -> list[dict]:
        if branch:
            rows = self.db.execute("SELECT * FROM commits WHERE branch=? ORDER BY date",
                                   (branch,)).fetchall()
        else:
            rows = self.db.execute("SELECT * FROM commits ORDER BY date").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["files"] = json.loads(d["files"] or "[]")
            out.append(d)
        return out


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _similar(a: str, b: str) -> bool:
    """Cheap similarity: high token overlap → treat as the same fact."""
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    jacc = len(ta & tb) / len(ta | tb)
    return jacc >= 0.72
