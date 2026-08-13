"""Team memory sharing: git-native per-author shards that merge without conflict,
author attribution, and incremental sync of teammates' memory each session."""
from __future__ import annotations

import subprocess
from pathlib import Path

from omni_memory import team, gitmeta, session_memory as sm
from omni_memory.store import Store, STORE_GITIGNORE


def _repo(tmp, name, email):
    d = tmp
    d.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(d), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(d), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.name", name], check=True)
    subprocess.run(["git", "-C", str(d), "config", "user.email", email], check=True)
    (d / "svc.py").write_text("def create_order():\n    return 1\n")
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-qm", "i"], check=True, capture_output=True)
    return d


def test_author_stamped_at_capture(repo):
    from omni_memory.store import Store
    s = Store(repo)                                # repo fixture: user t <t@t>
    m = sm.remember(s, repo, "create_order inserts the row", kind="flow",
                    files=["svc.py"], source="manual")
    assert s.get_memory(m.id)["author"] == "t <t@t>"


def test_shard_is_committable(tmp_path):
    """The store's .gitignore must carve out team/ so shards can be committed,
    while the live db stays ignored. (Uses a repo WITHOUT a root-level
    `.omni-memory/` ignore, which is how real installs look.)"""
    d = _repo(tmp_path / "solo", "Solo", "solo@x.io")
    Store(d)                                        # writes the managed .gitignore
    gi = (d / ".omni-memory" / ".gitignore").read_text()
    assert gi == STORE_GITIGNORE and "/team/" in gi
    s = Store(d)
    sm.remember(s, d, "a fact about svc", kind="fact", files=["svc.py"], source="manual")
    path = team.write_shard(s, d)
    repo = d
    rel = str(path.relative_to(repo))
    # git must NOT ignore the shard (carve-out works) but MUST ignore the db
    ignored_shard = subprocess.run(["git", "-C", str(repo), "check-ignore", rel],
                                   capture_output=True).returncode == 0
    ignored_db = subprocess.run(["git", "-C", str(repo), "check-ignore", ".omni-memory/omni.db"],
                                capture_output=True).returncode == 0
    assert not ignored_shard and ignored_db


def test_two_teammates_sync_conflict_free(tmp_path):
    # Alice and Bob each have their own clone + git identity
    alice = _repo(tmp_path / "alice", "Alice", "alice@x.io")
    bob = _repo(tmp_path / "bob", "Bob", "bob@x.io")
    sa, sb = Store(alice), Store(bob)

    sm.remember(sa, alice, "create_order publishes order.created", kind="flow",
                files=["svc.py"], symbols=["create_order"], source="manual")
    sm.remember(sb, bob, "never charge before the order row commits", kind="gotcha",
                files=["svc.py"], symbols=["create_order"], source="manual")

    pa, pb = team.write_shard(sa, alice), team.write_shard(sb, bob)
    # distinct filenames per author → no shared blob → merges without conflict
    assert pa.name != pb.name

    # Bob pulls Alice's shard into his team/ dir and syncs
    (bob / ".omni-memory" / "team").mkdir(parents=True, exist_ok=True)
    (bob / ".omni-memory" / "team" / pa.name).write_text(pa.read_text())
    added = team.sync(sb, bob)
    assert added == 1                              # Alice's memory imported
    texts = {m["text"]: m for m in sb.memories(status="active")}
    assert "create_order publishes order.created" in texts
    got = texts["create_order publishes order.created"]
    assert got["author"] == "Alice <alice@x.io>"   # attribution preserved
    assert got["source"] == "shared"               # tagged ↗external

    # incremental + idempotent: re-syncing adds nothing, and Bob's own shard is skipped
    assert team.sync(sb, bob) == 0


def test_sync_skips_own_shard(tmp_path):
    alice = _repo(tmp_path / "a2", "Alice", "alice@x.io")
    sa = Store(alice)
    sm.remember(sa, alice, "alpha decision about the outbox", kind="decision",
                files=["svc.py"], source="manual")
    team.write_shard(sa, alice)                     # my own shard sits in team/
    assert team.sync(sa, alice) == 0                # never re-imports my own memories
