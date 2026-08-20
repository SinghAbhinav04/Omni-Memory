"""Injection modes: memory is PULLED on demand by default (no per-prompt token
cost), seeded once at session start, and kept fresh — with `auto` still available
for those who want per-prompt enforcement."""
from __future__ import annotations

import io
import types

from omni_memory import cli
from omni_memory.store import Memory


def _hook(event, monkeypatch, stdin="{}"):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    cli._run_hook(types.SimpleNamespace(event=event))


def test_userprompt_is_silent_in_pull_mode(store, repo, monkeypatch, capsys):
    """The default (session) mode does NOT inject on every prompt — the whole point
    of pull mode is zero recurring token cost."""
    store.add_memory(Memory(text="auth uses jwt in a cookie", kind="decision", branch="main"))
    monkeypatch.chdir(repo)
    assert store.get_meta("inject_mode", "session") == "session"   # default
    _hook("inject", monkeypatch, '{"prompt": "how does auth jwt work"}')
    assert capsys.readouterr().out.strip() == ""                    # nothing injected


def test_auto_mode_injects_per_prompt(store, repo, monkeypatch, capsys):
    store.add_memory(Memory(text="auth uses jwt in a cookie", kind="decision", branch="main"))
    store.set_meta("inject_mode", "auto")
    monkeypatch.chdir(repo)
    _hook("inject", monkeypatch, '{"prompt": "how does auth jwt work"}')
    assert "VERIFIED PROJECT MEMORY" in capsys.readouterr().out     # control: it CAN inject


def test_session_start_seeds_once(store, repo, monkeypatch, capsys):
    store.add_memory(Memory(text="auth uses jwt in a cookie", kind="decision", branch="main"))
    monkeypatch.chdir(repo)
    _hook("start", monkeypatch)
    assert "VERIFIED PROJECT MEMORY" in capsys.readouterr().out     # seeded at start


def test_manual_mode_never_seeds(store, repo, monkeypatch, capsys):
    store.add_memory(Memory(text="auth uses jwt in a cookie", kind="decision", branch="main"))
    store.set_meta("inject_mode", "manual")
    monkeypatch.chdir(repo)
    _hook("start", monkeypatch)
    assert "VERIFIED PROJECT MEMORY" not in capsys.readouterr().out  # pull-only, no seed


def test_config_sets_inject_mode(store, repo, monkeypatch):
    """0.10.0 folded the bespoke `inject-mode` subcommand into `config`, so every
    tunable is listed in one place instead of one command per knob."""
    monkeypatch.chdir(repo)
    cli.cmd_config(types.SimpleNamespace(key="inject_mode", value="auto"))
    assert store.get_meta("inject_mode") == "auto"
    cli.cmd_config(types.SimpleNamespace(key="inject_mode", value="manual"))
    assert store.get_meta("inject_mode") == "manual"


def test_config_rejects_an_invalid_mode(store, repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    cli.cmd_config(types.SimpleNamespace(key="inject_mode", value="auto"))
    assert cli.cmd_config(types.SimpleNamespace(key="inject_mode", value="loud")) == 1
    assert store.get_meta("inject_mode") == "auto"        # unchanged by the bad value
    assert "one of" in capsys.readouterr().out


def test_config_reads_a_setting_without_changing_it(store, repo, monkeypatch):
    monkeypatch.chdir(repo)
    cli.cmd_config(types.SimpleNamespace(key="inject_mode", value="manual"))
    cli.cmd_config(types.SimpleNamespace(key="inject_mode", value=None))
    assert store.get_meta("inject_mode") == "manual"
