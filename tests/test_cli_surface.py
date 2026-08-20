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
