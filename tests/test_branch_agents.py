"""Git provenance, branch classification, and the cross-IDE AGENTS.md."""
import subprocess

from omni_memory import gitmeta, branch as branchmod, agentsmd
from omni_memory.store import Memory


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)


def test_state_signature_stable_then_moves(repo):
    s1 = gitmeta.state_signature(repo)
    assert s1 and s1 == gitmeta.state_signature(repo)
    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\n# touch\n")
    assert gitmeta.state_signature(repo) != s1


def test_list_branches_includes_remote_only(repo):
    _git(repo, "branch", "feature")
    _git(repo, "update-ref", "refs/remotes/origin/pushed-only", "HEAD")
    local = set(gitmeta.list_branches(repo))
    allb = set(gitmeta.list_branches(repo, include_remotes=True))
    assert "feature" in local
    assert "origin/pushed-only" in allb and "origin/pushed-only" not in local


def test_full_refresh_populates_everything(store, repo):
    branchmod.full_refresh(store, repo)
    assert store.has_code_graph()
    assert any(b["name"] == "main" for b in store.branches())
    assert (repo / "AGENTS.md").exists()


def test_classify_marks_abandoned(store, repo):
    # a memory tagged to a branch that never existed in git → abandoned
    store.add_memory(Memory(text="wip note", kind="todo", branch="dead-branch"))
    branchmod.classify_branches(store, repo)
    row = store.db.execute("SELECT status FROM branches WHERE name='dead-branch'").fetchone()
    assert row and row["status"] == "abandoned"


def test_agents_md_managed_block(store, repo):
    store.add_memory(Memory(text="Auth uses JWT in an httpOnly cookie",
                            kind="decision", branch="main"))
    path = agentsmd.write(store, repo)
    txt = path.read_text()
    assert agentsmd.START in txt and agentsmd.END in txt
    assert "JWT" in txt and "omni-memory inject" in txt


def test_agents_md_preserves_user_content_and_idempotent(store, repo):
    p = repo / "AGENTS.md"
    p.write_text("# My rules\n\nUse tabs.\n")
    agentsmd.write(store, repo)
    agentsmd.write(store, repo)                     # twice → no duplicate block
    txt = p.read_text()
    assert "Use tabs." in txt
    assert txt.count(agentsmd.START) == 1
