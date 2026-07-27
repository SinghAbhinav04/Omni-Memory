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
  line_start INTEGER, line_end INTEGER, parent TEXT);
CREATE TABLE IF NOT EXISTS code_edges(src TEXT, dst TEXT, rel TEXT);
CREATE INDEX IF NOT EXISTS idx_mem_branch ON memory(branch);
CREATE INDEX IF NOT EXISTS idx_mem_status ON memory(status);
CREATE INDEX IF NOT EXISTS idx_cedge_dst ON code_edges(dst);
CREATE INDEX IF NOT EXISTS idx_cnode_file ON code_nodes(file);
CREATE INDEX IF NOT EXISTS idx_cnode_name ON code_nodes(name);
"""


class Store:
    def __init__(self, root: Optional[Path] = None):
        self.dir = store_dir(root)
        # check_same_thread=False: the dashboard server handles requests on
        # worker threads but shares one read-mostly connection.
        self.db = sqlite3.connect(self.dir / "omni.db", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first schema (SQLite lacks IF NOT
        EXISTS on ADD COLUMN, so check PRAGMA and add what's missing)."""
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(memory)")}
        for col, decl in (("stale", "INTEGER DEFAULT 0"),
                          ("stale_since", "REAL"), ("stale_files", "TEXT")):
            if col not in have:
                self.db.execute(f"ALTER TABLE memory ADD COLUMN {col} {decl}")

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

    def memories(self, branch: Optional[str] = None, base: Optional[str] = None,
                 kinds: Optional[list[str]] = None, files: Optional[list[str]] = None,
                 status: str = "active", query: str = "", limit: int = 200) -> list[dict]:
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
        if query or files:
            from . import rank as _rank
            rows = _rank.rank(rows, query, files=files)
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

    def forget(self, mem_id: str) -> bool:
        cur = self.db.execute("UPDATE memory SET status='abandoned',updated=? WHERE id=?",
                              (time.time(), mem_id))
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
        return d

    # -- code graph ---------------------------------------------------------
    def replace_code_graph(self, nodes: list[dict], edges: list[dict]) -> None:
        self.db.execute("DELETE FROM code_nodes")
        self.db.execute("DELETE FROM code_edges")
        self.db.executemany(
            "INSERT OR REPLACE INTO code_nodes VALUES(?,?,?,?,?,?,?)",
            [(n["id"], n["kind"], n["name"], n["file"], n["line_start"],
              n["line_end"], n.get("parent")) for n in nodes])
        self.db.executemany("INSERT INTO code_edges VALUES(?,?,?)",
                            [(e["src"], e["dst"], e["rel"]) for e in edges])
        self.db.commit()

    def code_graph(self) -> tuple[list[dict], list[dict]]:
        nodes = [dict(r) for r in self.db.execute("SELECT * FROM code_nodes")]
        edges = [dict(r) for r in self.db.execute("SELECT * FROM code_edges")]
        return nodes, edges

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
