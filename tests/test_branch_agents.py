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


def test_refresh_if_stale_skips_when_unchanged(store, repo):
    assert branchmod.refresh_if_stale(store, repo) is True   # cold → build
    assert branchmod.refresh_if_stale(store, repo) is False  # unchanged → skip
    (repo / "svc.py").write_text((repo / "svc.py").read_text() + "\ndef added():\n    return 0\n")
    assert branchmod.refresh_if_stale(store, repo) is True   # changed → rebuild


def test_hook_never_raises(store, repo, monkeypatch):
    """A failing hook must degrade to a no-op (return 0), never crash the session."""
    import io
    import omni_memory.cli as cli
    import omni_memory.inject as inject
    monkeypatch.chdir(repo)
    monkeypatch.setattr(inject, "build_block",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("sys.stdin", io.StringIO('{"prompt": "hi"}'))

    class Args:
        event = "inject"
    assert cli.cmd_hook(Args()) == 0


def test_pipeline_survives_ascii_locale(store, repo):
    """Windows defaults to cp1252 and crashes on Unicode I/O ('charmap codec').
    Simulate with LC_ALL=C: the full pipeline (git subprocess, code graph read,
    AGENTS.md/MEMORY.md write) must run without a decode/encode error even with a
    Unicode-heavy memory."""
    import os
    import subprocess
    import sys
    from omni_memory.store import Memory
    store.add_memory(Memory(text="flow: publish order.created → Kafka · café ⚠ 🌐",
                            kind="flow", branch="main"))
    code = (
        "from omni_memory import branch\n"
        "from omni_memory.store import Store\n"
        f"r = r'''{repo}'''\n"
        "branch.full_refresh(Store(r), r)\n"
        "print('OK')\n"
    )
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
               PYTHONPATH=os.pathsep.join(sys.path))
    res = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, env=env)
    assert res.returncode == 0, res.stderr
    assert "OK" in res.stdout


def test_opencode_bind_writes_agents_md(store, repo, monkeypatch):
    from omni_memory import install
    monkeypatch.chdir(repo)
    (repo / "opencode.json").write_text("{}\n", encoding="utf-8")
    assert install.detect_ide(repo) == "opencode"      # auto-detected
    assert install.bind("opencode") == 0
    txt = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "OMNI-MEMORY:START" in txt                   # context written for OpenCode


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
