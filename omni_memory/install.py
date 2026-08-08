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


def install(platform: str = "claude-code") -> int:
    if platform == "claude-code":
        return _install_claude_code()
    if platform == "antigravity":
        return _install_antigravity()
    print(f"Unknown platform '{platform}'. Try: claude-code | antigravity")
    return 1


def detect_ide(root: Path) -> str:
    """Best-guess which IDE this project uses, from files it leaves behind.
    Falls back to 'claude-code' (the richest integration) when unsure."""
    if (root / ".claude").exists() or (root / "CLAUDE.md").exists():
        return "claude-code"
    if (root / ".antigravity").exists() or (root / ".gemini").exists():
        return "antigravity"
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


def _install_antigravity() -> int:
    """Antigravity reads AGENTS.md at the repo root as project context, so the
    canonical file is the portable integration. (MCP server is the richer P1
    path; the file works today with zero config.)"""
    proj = find_project_root()
    _write_agents_md(proj)
    print("[+] Antigravity picks up AGENTS.md automatically as project context.")
    print("    Keep it fresh with either:")
    print("      • `omni-memory ui` running (the watcher refreshes AGENTS.md live), or")
    print("      • `omni-memory hook start` at session start (or any capture/build).")
    print("    Optional headless capture: set OMNI_AGENT_CMD to Antigravity's CLI.")
    return 0
