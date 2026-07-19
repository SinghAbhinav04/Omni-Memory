"""Git provenance — branch creator, timeline, merge topology, commits.

Everything is read-only via the `git` CLI. Powers branch-aware memory and the
Repo Graph view.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional


def _git(root: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def is_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree") == "true"


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "main"


def default_branch(root: Path) -> str:
    ref = _git(root, "symbolic-ref", "refs/remotes/origin/HEAD")
    if ref:
        return ref.rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _git(root, "rev-parse", "--verify", cand):
            return cand
    return "main"


def list_branches(root: Path) -> list[str]:
    out = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [b for b in out.splitlines() if b]


def branch_creator(root: Path, branch: str, base: str) -> tuple[str, float]:
    """(author, unix_time) of the branch's first unique commit vs base."""
    rng = f"{base}..{branch}" if base and base != branch else branch
    line = _git(root, "log", rng, "--reverse",
                "--format=%an|%at", "--max-count=1")
    if not line and branch:  # branch with no unique commits → its tip
        line = _git(root, "log", branch, "--format=%an|%at", "--max-count=1")
    if "|" in line:
        an, at = line.split("|", 1)
        try:
            return an, float(at)
        except ValueError:
            return an, 0.0
    return "", 0.0


def ahead_behind(root: Path, branch: str, base: str) -> tuple[int, int]:
    out = _git(root, "rev-list", "--left-right", "--count", f"{base}...{branch}")
    parts = out.split()
    if len(parts) == 2:
        try:
            behind, ahead = int(parts[0]), int(parts[1])
            return ahead, behind
        except ValueError:
            pass
    return 0, 0


def merge_info(root: Path, branch: str, into: str) -> tuple[bool, str, float]:
    """Is `branch` merged into `into`? Returns (merged, merge_commit, when)."""
    merged_list = _git(root, "branch", "--merged", into).splitlines()
    merged = any(b.strip().lstrip("* ").strip() == branch for b in merged_list)
    if not merged:
        return False, "", 0.0
    tip = _git(root, "rev-parse", branch)
    # find the merge commit on `into` that brought this branch in
    mc = _git(root, "log", into, "--merges", "--format=%H|%at",
              f"--grep={branch}", "--max-count=1")
    sha, when = "", 0.0
    if "|" in mc:
        sha, w = mc.split("|", 1)
        try:
            when = float(w)
        except ValueError:
            when = 0.0
    return True, (sha or tip), when


def commits_on(root: Path, branch: str, base: str, limit: int = 200) -> list[dict]:
    rng = f"{base}..{branch}" if base and base != branch else branch
    out = _git(root, "log", rng, f"--max-count={limit}",
               "--format=%H|%an|%at|%s")
    rows = []
    for line in out.splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            sha, an, at, msg = parts
            try:
                date = float(at)
            except ValueError:
                date = 0.0
            files = _git(root, "show", "--name-only", "--format=", sha).splitlines()
            rows.append({"sha": sha, "author": an, "date": date, "message": msg,
                         "files": [f for f in files if f]})
    return rows


def snapshot(root: Path) -> dict:
    """Full branch/commit topology for the store + Repo Graph."""
    if not is_repo(root):
        return {"branches": [], "commits": [], "default": "main", "current": "main"}
    base = default_branch(root)
    cur = current_branch(root)
    branches, commits = [], []
    for b in list_branches(root):
        creator, created_at = branch_creator(root, b, base if b != base else "")
        ahead, behind = (0, 0) if b == base else ahead_behind(root, b, base)
        merged, mc, when = (False, "", 0.0) if b == base else merge_info(root, b, base)
        branches.append({
            "name": b, "creator": creator, "created_at": created_at,
            "base_branch": None if b == base else base,
            "ahead": ahead, "behind": behind,
            "status": "merged" if merged else "active",
            "merged_at": when, "merge_commit": mc,
            "into_branch": base if merged else None,
        })
        for c in commits_on(root, b, base if b != base else "", limit=100):
            c["branch"] = b
            commits.append(c)
    return {"branches": branches, "commits": commits, "default": base, "current": cur}
