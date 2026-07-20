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

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_SRC = REPO_ROOT / "skills" / "omni-memory"


def _hook_cmd(event: str) -> str:
    """Absolute, PATH-independent command (the shell fn isn't available to hooks)."""
    return (f'PYTHONPATH="{REPO_ROOT}" "{sys.executable}" -m omni_memory hook {event}')


def _hooks_block() -> dict:
    return {
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
            data = json.loads(settings.read_text())
        except Exception:  # noqa: BLE001
            data = {}
    hooks = data.setdefault("hooks", {})
    for event, val in _hooks_block().items():
        existing = [h for h in hooks.get(event, [])
                    if "omni_memory hook" not in json.dumps(h)]
        hooks[event] = existing + val
    settings.write_text(json.dumps(data, indent=2))
    print(f"[+] hooks written → {settings}")
    print("    UserPromptSubmit → injects verified memory (enforced)")
    print("    SessionEnd       → captures the session (via your agent, no key)")
    print("\n[+] Restart Claude Code in this project. Then just work — memory")
    print("    injects every prompt and updates itself when the session ends.")
    return 0


def _install_antigravity() -> int:
    print("[+] Antigravity: register OmniMemory as an MCP server (P1).")
    print("    Meanwhile: the MEMORY.md digest can be added as a persistent artifact,")
    print(f"    and the skill/config lives at: {SKILL_SRC}")
    print("    Set OMNI_AGENT_CMD to Antigravity's CLI for headless capture.")
    return 0
