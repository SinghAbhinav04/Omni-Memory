"""Wire OmniMemory into a coding agent (Claude Code first; Antigravity via MCP).

Writes hooks to the PROJECT's .claude/settings.json (scoped + safe, backed up),
so capture/inject fire automatically without touching your global config.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

from .store import find_project_root

PKG_ROOT = Path(__file__).resolve().parent          # the installed omni_memory/ package
SKILL_SRC = PKG_ROOT / "skills" / "omni-memory"     # skill ships inside the wheel


def _hook_cmd(event: str) -> str:
    """Absolute, PATH-independent command (the shell fn isn't available to hooks).

    `-m omni_memory` runs against the interpreter OmniMemory was installed under,
    so it works whether installed from PyPI, `pip install -e .`, or a source tree
    on PYTHONPATH."""
    return f'PYTHONPATH="{PKG_ROOT.parent}" "{sys.executable}" -m omni_memory hook {event}'


def _hooks_block() -> dict:
    return {
        "SessionStart": [{"hooks": [{"type": "command", "command": _hook_cmd("start")}]}],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": _hook_cmd("inject")}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": _hook_cmd("capture")}]}],
    }


# IDEs that read the repo-root AGENTS.md as project context. For these the
# canonical file IS the integration — no bespoke hook system to wire. OpenCode
# gets that PLUS a native plugin (below), so it's handled separately.
_AGENTS_IDES = ("antigravity", "cursor", "windsurf")


# OpenCode plugin (.opencode/plugins/omnimemory.js). Grounded in the OpenCode
# plugin API: a plugin exports async ({ $, directory }) => ({ hooks }); real
# hooks include session.created / session.idle and experimental.session.
# compacting, which is the one that can push into the model's context.
_OPENCODE_PLUGIN = r'''// OmniMemory — OpenCode plugin
// Keeps this project's verified memory fresh and re-injects it so it survives
// context compaction. Requires the CLI:  pip install omni-memory-agent
// https://github.com/SinghAbhinav04/Omni-Memory
export const OmniMemory = async ({ $, directory }) => {
  const refresh = async () => {
    // rebuild-if-changed + refresh the AGENTS.md context this session reads
    try { await $`echo {} | omni-memory hook start`.cwd(directory).quiet(); }
    catch (e) { /* CLI absent or not a project — ignore */ }
  };
  return {
    "session.created": refresh,   // fresh memory at the start of every session
    "session.idle": refresh,      // …and kept current as you work
    // when OpenCode compacts the conversation, re-inject verified project memory
    // so it is not lost across the summary boundary
    "experimental.session.compacting": async (_input, output) => {
      try {
        const res = await $`omni-memory inject`.cwd(directory).quiet();
        const block = res.stdout.toString().trim();
        if (block) output.context.push(block);
      } catch (e) { /* ignore */ }
    },
  };
};
'''


def install(platform: str = "claude-code") -> int:
    if platform == "claude-code":
        return _install_claude_code()
    if platform == "opencode":
        return _install_opencode()
    if platform in _AGENTS_IDES:
        return _install_agents_ide(platform)
    print(f"Unknown platform '{platform}'. "
          f"Try: claude-code | opencode | {' | '.join(_AGENTS_IDES)}")
    return 1


def _install_opencode() -> int:
    """OpenCode: write the native plugin (.opencode/plugins/omnimemory.js) so
    memory refreshes each session and survives compaction, plus the AGENTS.md
    that carries per-message context."""
    proj = find_project_root()
    _write_agents_md(proj)
    pdir = proj / ".opencode" / "plugins"
    try:
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "omnimemory.js").write_text(_OPENCODE_PLUGIN, encoding="utf-8")
        print(f"[+] OpenCode plugin → {pdir / 'omnimemory.js'}")
    except Exception as e:  # noqa: BLE001
        print(f"[!] couldn't write the OpenCode plugin ({e})")
    print("    Needs the CLI:  pip install omni-memory-agent")
    print("    The plugin refreshes memory each session and re-injects it on")
    print("    compaction; per-message context comes from AGENTS.md.")
    print("    Restart OpenCode to load the plugin.")
    return 0


def detect_ide(root: Path) -> str:
    """Best-guess which IDE this project uses, from files it leaves behind.
    Falls back to 'claude-code' (the richest integration) when unsure."""
    if (root / ".claude").exists() or (root / "CLAUDE.md").exists():
        return "claude-code"
    if (root / ".opencode").exists() or (root / "opencode.json").exists() \
            or (root / "opencode.jsonc").exists():
        return "opencode"
    if (root / ".antigravity").exists() or (root / ".gemini").exists():
        return "antigravity"
    if (root / ".cursor").exists():
        return "cursor"
    if (root / ".windsurf").exists() or (root / ".windsurfrules").exists():
        return "windsurf"
    return "claude-code"


def bind(ide: str = "auto") -> int:
    """One-command onboarding: wire OmniMemory into the given IDE (or auto-detect),
    which for every IDE writes/refreshes the repo-root AGENTS.md and, where the IDE
    supports it (Claude Code), also installs the session hooks."""
    root = find_project_root()
    if ide in ("", "auto"):
        ide = detect_ide(root)
        print(f"[*] auto-detected IDE: {ide}  (override: omni-memory bind <ide>)")
    return install(platform=ide)


def _install_claude_code() -> int:
    # 1) link the skill globally
    skills = Path.home() / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    dest = skills / "omni-memory"
    try:
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.symlink_to(SKILL_SRC)
        print(f"[+] skill linked → {dest}")
    except Exception as e:  # noqa: BLE001
        print(f"[!] couldn't link skill ({e}); copy {SKILL_SRC} → {dest} manually.")

    # 2) write project-scoped hooks (safe, backed up, merged)
    proj = find_project_root()
    settings = proj / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if settings.exists():
        shutil.copy2(settings, settings.with_suffix(f".json.bak-{int(time.time())}"))
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    hooks = data.setdefault("hooks", {})
    for event, val in _hooks_block().items():
        existing = [h for h in hooks.get(event, [])
                    if "omni_memory hook" not in json.dumps(h)]
        hooks[event] = existing + val
    settings.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"[+] hooks written → {settings}")
    print("    SessionStart     → refreshes + seeds verified memory (incremental)")
    print("    UserPromptSubmit → injects verified memory (enforced)")
    print("    SessionEnd       → captures the session (via your agent, no key)")
    _write_agents_md(proj)
    print("\n[+] Restart Claude Code in this project. Then just work — memory")
    print("    injects every prompt and updates itself when the session ends.")
    return 0


def _write_agents_md(proj: Path) -> None:
    """Create/refresh the canonical AGENTS.md every AI IDE reads on session start."""
    try:
        from . import agentsmd
        from .store import Store
        path = agentsmd.write(Store(proj), proj)
        print(f"[+] canonical context written → {path}  (read by any AI IDE)")
    except Exception as e:  # noqa: BLE001
        print(f"[!] couldn't write AGENTS.md ({e})")


def _install_agents_ide(ide: str) -> int:
    """Wire an AGENTS.md-reading IDE (OpenCode, Antigravity, Cursor, Windsurf).
    These have no Claude-Code-style hook system, so the integration is the
    canonical AGENTS.md (which they load as project context) plus the agent
    driving the `omni-memory` CLI in-session. We write/refresh the file and
    print exactly what to do next."""
    proj = find_project_root()
    _write_agents_md(proj)
    label = {"opencode": "OpenCode", "antigravity": "Antigravity",
             "cursor": "Cursor", "windsurf": "Windsurf"}.get(ide, ide)
    print(f"[+] {label} reads AGENTS.md at the repo root as project context —")
    print("    it picks up your memory automatically on the next session.")
    print("    In-session, the agent uses the CLI for the ranked / fresh memory:")
    print("      • start of a task → `omni-memory inject \"<the request>\"` (or `recall`)")
    print("      • learned something durable → `omni-memory remember \"…\" --kind <kind>`")
    print("      • end of a task → `omni-memory prompt session` → `omni-memory capture`")
    print("    Keep AGENTS.md fresh: run `omni-memory ui` (live), or `omni-memory")
    print("    build` / `omni-memory hook start`. (Automatic per-prompt hooks are")
    print("    Claude-Code-only today — here it's AGENTS.md + the CLI, agent-driven.)")
    return 0
