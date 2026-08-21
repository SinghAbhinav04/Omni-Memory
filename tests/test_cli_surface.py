"""The CLI surface is a contract — with users, with the docs, and with the IDEs.

0.10.0 cut 40 subcommands to 29 by folding duplicates into flags. The removals are
deliberate and unaliased, so the failure is loud. Two things are pinned here: what was
removed stays removed, and what the installed hooks call keeps working — a renamed hook
event would silently unwire every existing integration without any test noticing.
"""
import pytest

from omni_memory import cli, install


REMOVED = ["install", "digest", "map", "artifact", "prompt", "usage", "unlock",
           "team", "export", "import", "restore", "inject-mode", "branch-aware"]

SURVIVING = ["status", "doctor", "config", "on", "off", "bind", "key",
             "remember", "recall", "inject", "used", "forget", "lock",
             "build", "check", "capture", "gc", "flush",
             "systemmap", "ui", "gain", "branches",
             "conflicts", "resolve", "history",
             "share", "sync", "snapshot", "hook"]


@pytest.mark.parametrize("name", REMOVED)
def test_removed_commands_are_gone(name):
    """argparse exits 2 on an unknown subcommand — a loud failure, which is the point
    of removing rather than aliasing."""
    with pytest.raises(SystemExit) as e:
        cli.main([name])
    assert e.value.code == 2


def _registered_subcommands(monkeypatch):
    """Every subparser name the real parser registers, without running a command."""
    import argparse
    seen = set()
    real_add = argparse._SubParsersAction.add_parser

    def spy(self, name, **kw):
        seen.add(name)
        return real_add(self, name, **kw)

    monkeypatch.setattr(argparse._SubParsersAction, "add_parser", spy)
    with pytest.raises(SystemExit):        # --version exits after the parser is built
        cli.main(["--version"])
    return seen


def test_command_surface_is_exactly_as_declared(monkeypatch):
    """Pinned as equality, not membership: a command quietly added back (or a new one
    slipped in undocumented) should fail here, not drift into the docs later."""
    assert _registered_subcommands(monkeypatch) == set(SURVIVING)


def test_hook_events_still_match_what_install_wires():
    """`install._hooks_block()` embeds these event names as strings in the config it
    writes, and the published plugin's hooks.json does the same. Renaming one would
    leave every already-installed integration calling a command that no longer exists."""
    wired = set()
    for entries in install._hooks_block().values():
        for entry in entries:
            for h in entry["hooks"]:
                wired.add(h["command"].rsplit(" ", 1)[-1])
    assert wired == {"start", "inject", "precompact", "capture", "read"}


def test_hook_is_hidden_from_help(capsys):
    """It is a machine entrypoint; listing it invites people to run it by hand."""
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert "systemmap" in out                    # the listing rendered
    assert "==SUPPRESS==" not in out             # argparse 3.9 leaks this literal
    assert "\n    hook " not in out              # not described in the subcommand list


# ── an install written by an older version is not the same as a current one ──

def test_wired_events_are_parsed_not_grepped(tmp_path):
    """`doctor` used to test for the substring `omni_memory hook` in settings.json, which
    answers "at least one event" — the same answer for a complete install and for one
    missing every event added since. This repo was running on exactly that: three events
    wired, PostToolUse absent, an empty read ledger, and a green hook line."""
    import json
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {
        "SessionStart": [{"hooks": [{"type": "command",
                                     "command": "python3 -m omni_memory hook start"}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command",
                                         "command": "python3 -m omni_memory hook inject"}]}],
        "SessionEnd": [{"hooks": [{"type": "command",
                                   "command": "python3 -m omni_memory hook capture"}]}],
    }}))
    have = cli._wired_events(s)
    assert have == {"SessionStart", "UserPromptSubmit", "SessionEnd"}
    assert set(install._hooks_block()) - have == {"PostToolUse", "PreCompact"}


def test_a_complete_install_reports_nothing_missing(tmp_path):
    """The control: what `bind` writes today must reconcile clean against itself, or the
    warning above fires on every healthy setup and gets ignored."""
    import json
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": install._hooks_block()}))
    assert cli._wired_events(s) == set(install._hooks_block())


def test_foreign_hooks_are_not_counted_as_ours(tmp_path):
    """Someone else's PostToolUse hook must not make our integration look installed."""
    import json
    s = tmp_path / "settings.json"
    s.write_text(json.dumps({"hooks": {
        "PostToolUse": [{"hooks": [{"type": "command", "command": "some-other-tool run"}]}]}}))
    assert cli._wired_events(s) == set()


def test_a_missing_or_broken_settings_file_wires_nothing(tmp_path):
    assert cli._wired_events(tmp_path / "nope.json") == set()
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert cli._wired_events(broken) == set()
