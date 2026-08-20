"""Git provenance — branch creator, timeline, merge topology, commits.

Everything is read-only via the `git` CLI. Powers branch-aware memory and the
Repo Graph view.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional


def _git(root: Path, *args: str) -> str:
    try:
        # encoding utf-8 + replace: git output (filenames, messages, diffs) is
        # utf-8, but text=True would decode with the OS locale (cp1252 on Windows)
        # and crash on any non-ASCII byte.
        r = subprocess.run(["git", "-C", str(root), *args],
                           capture_output=True, text=True, timeout=15,
                           encoding="utf-8", errors="replace")
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def is_repo(root: Path) -> bool:
    return _git(root, "rev-parse", "--is-inside-work-tree") == "true"


def current_branch(root: Path) -> str:
    return _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "main"


def git_user(root: Path) -> str:
    """The committing identity ("Name <email>"), for authoring shared memory."""
    name = _git(root, "config", "user.name")
    email = _git(root, "config", "user.email")
    if name and email:
        return f"{name} <{email}>"
    return email or name or ""


def blob_sha(root: Path, path: str) -> str:
    """Git blob id of the bytes of `path` AS THEY EXIST IN THE WORKING TREE — i.e.
    what the agent actually read, committed or not. `git hash-object` computes the
    same id git would assign that content, so provenance binds to the *observed*
    bytes rather than the committed HEAD blob (which can differ mid-session, when the
    working tree is dirty) — and it also covers uncommitted / untracked files, which
    a `HEAD:<path>` lookup misses entirely. '' if the path is absent. Identical
    content → identical id, so a changed id is exact drift and a missing file is exact
    deletion — no diff heuristic, no TTL."""
    if not (root / path).is_file():
        return ""
    sha = _git(root, "hash-object", "--", path)
    return sha if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha) else ""


def blob_shas(root: Path, paths) -> dict:
    """{path: observed-content id} for the existing files among `paths` — the digest
    of the working-tree bytes the agent saw at capture (see `blob_sha`), so staleness
    is verified against the actual observation, including uncommitted work.

    Hashes in ONE `git hash-object` call rather than one per path. This rides the
    injection hot path (every pull pins what it is trusted on), where a subprocess per
    file was ~13x slower and made a large pull visibly stall.
    """
    live = [p for p in (paths or []) if (root / p).is_file()]
    if not live:
        return {}
    out = _git(root, "hash-object", "--", *live).splitlines()
    if len(out) != len(live):                 # partial/failed batch → fall back per-path
        return {p: s for p in live if (s := blob_sha(root, p))}
    return {p: sha for p, sha in zip(live, out)
            if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)}


def default_branch(root: Path) -> str:
    ref = _git(root, "symbolic-ref", "refs/remotes/origin/HEAD")
    if ref:
        return ref.rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _git(root, "rev-parse", "--verify", cand):
            return cand
    return "main"


def list_branches(root: Path, include_remotes: bool = False) -> list[str]:
    """Local branches (refs/heads). With include_remotes, also append remote-only
    branches (e.g. `origin/feature` that was pushed but never checked out locally)
    so the topology reflects every branch that exists, not just the checked-out
    ones. Remote refs are returned by their full short name (`origin/x`) — which is
    still a resolvable git ref — and skipped when a local branch shadows them."""
    out = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    locals_ = [b for b in out.splitlines() if b]
    if not include_remotes:
        return locals_
    localset = set(locals_)
    rout = _git(root, "for-each-ref", "--format=%(refname:short)", "refs/remotes")
    extra = []
    for r in (x.strip() for x in rout.splitlines()):
        if not r or r.endswith("/HEAD"):
            continue
        short = r.split("/", 1)[1] if "/" in r else r  # strip remote prefix
        if short in localset or short == "HEAD":
            continue
        extra.append(r)
    return locals_ + extra


def state_signature(root: Path) -> str:
    """A cheap fingerprint of git state that changes on anything the dashboard
    cares about: new commit, branch create/delete/switch, remote update, or a
    working-tree edit. The auto-refresh watcher polls this and only does the
    expensive rebuild when it moves — so it stays live without busy-work."""
    if not is_repo(root):
        return ""
    head = _git(root, "rev-parse", "HEAD")
    refs = _git(root, "for-each-ref", "--format=%(refname) %(objectname)",
                "refs/heads", "refs/remotes")
    status = _git(root, "status", "--porcelain")
    return hashlib.sha1(f"{head}\n{refs}\n{status}".encode()).hexdigest()


def tip_time(root: Path, branch: str) -> float:
    """Unix time of a branch's tip commit (0 if unknown)."""
    out = _git(root, "log", "-1", "--format=%at", branch)
    try:
        return float(out)
    except ValueError:
        return 0.0


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


def merged_branches(root: Path, into: str) -> set:
    """Branch names already merged into `into` (one call, hoisted for big repos)."""
    out = _git(root, "branch", "--merged", into)
    return {ln.strip().lstrip("* ").strip() for ln in out.splitlines() if ln.strip()}


def merge_info(root: Path, branch: str, into: str,
               merged_set: Optional[set] = None) -> tuple[bool, str, float]:
    """Is `branch` merged into `into`? Returns (merged, merge_commit, when).

    `merged_set`, when provided, is the precomputed set of branches merged into
    `into` (one `git branch --merged` call hoisted out of the per-branch loop —
    otherwise this is O(branches²) on big repos)."""
    if merged_set is None:
        merged_set = merged_branches(root, into)
    if branch not in merged_set:
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


def is_squash_merged(root: Path, branch: str, base: str) -> bool:
    """True if `branch` is effectively merged into `base` via SQUASH or REBASE —
    the common PR workflow that `git branch --merged` misses (no merge commit).

    `git cherry <base> <branch>` marks each of the branch's commits `-` if an
    equivalent patch already exists in base, `+` if not. If nothing is `+`, every
    change is already in base → merged. Only call this for branches with a small
    `ahead` count (the caller gates it) so it stays cheap."""
    out = _git(root, "cherry", base, branch)
    if not out:
        return False
    lines = [ln for ln in out.splitlines() if ln.strip()]
    return bool(lines) and not any(ln.startswith("+") for ln in lines)


# A branch this far ahead of base is genuinely divergent, not a squashed PR —
# skip the (per-branch) cherry check to keep `snapshot` cheap on big repos.
_SQUASH_AHEAD_MAX = 30


def commits_on(root: Path, branch: str, base: str, limit: int = 200) -> list[dict]:
    """Commits (with changed files) unique to `branch` vs `base`, in ONE git call.

    A header line (prefixed with \\x1e) carries the metadata; the file names for
    that commit follow on their own lines until the next header. This replaces a
    per-commit `git show`, which was O(commits) subprocess spawns and hung on
    large repos."""
    rng = f"{base}..{branch}" if base and base != branch else branch
    # Plain-text marker (not a control char): str.splitlines() splits on \x1c-\x1e
    # etc., which silently ate a record-separator marker here.
    out = _git(root, "log", rng, f"--max-count={limit}",
               "--name-only", "--format=\x01OMNI\x01%H|%an|%at|%s")
    rows: list[dict] = []
    cur: Optional[dict] = None
    for line in out.splitlines():
        if line.startswith("\x01OMNI\x01"):
            if cur:
                rows.append(cur)
            parts = line[6:].split("|", 3)
            if len(parts) == 4:
                sha, an, at, msg = parts
                try:
                    date = float(at)
                except ValueError:
                    date = 0.0
                cur = {"sha": sha, "author": an, "date": date, "message": msg,
                       "files": []}
            else:
                cur = None
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    if cur:
        rows.append(cur)
    return rows


def snapshot(root: Path) -> dict:
    """Full branch/commit topology for the store + Repo Graph."""
    if not is_repo(root):
        return {"branches": [], "commits": [], "default": "main", "current": "main"}
    base = default_branch(root)
    cur = current_branch(root)
    merged_set = merged_branches(root, base)  # one call, not per-branch
    branches, commits = [], []
    for b in list_branches(root, include_remotes=True):
        creator, created_at = branch_creator(root, b, base if b != base else "")
        ahead, behind = (0, 0) if b == base else ahead_behind(root, b, base)
        # a branch with no unique commits sitting on base is "fresh", not merged
        if b == base or (ahead == 0 and behind == 0):
            merged, mc, when = False, "", 0.0
        else:
            merged, mc, when = merge_info(root, b, base, merged_set=merged_set)
            # catch squash/rebase merges that `git branch --merged` can't see —
            # only for branches with a few unique commits (cheap `git cherry`).
            if not merged and 0 < ahead <= _SQUASH_AHEAD_MAX \
                    and is_squash_merged(root, b, base):
                merged, mc, when = True, _git(root, "rev-parse", b), 0.0
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
