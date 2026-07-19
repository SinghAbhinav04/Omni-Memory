"""Wire OmniMemory into a coding agent (Claude Code first; Antigravity via MCP)."""
from __future__ import annotations

import json
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_SRC = REPO_ROOT / "skills" / "omni-memory"

HOOKS = {
    "UserPromptSubmit": [{"hooks": [{"type": "command",
        "command": "omni-memory inject \"$CLAUDE_USER_PROMPT\""}]}],
    "SessionEnd": [{"hooks": [{"type": "command",
        "command": "omni-memory capture < \"$CLAUDE_TRANSCRIPT_PATH\""}]}],
}


def install(platform: str = "claude-code") -> int:
    if platform == "claude-code":
        return _install_claude_code()
    if platform == "antigravity":
        return _install_antigravity()
    print(f"Unknown platform '{platform}'. Try: claude-code | antigravity")
    return 1


def _install_claude_code() -> int:
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

    print("\n[+] Add these hooks to ~/.claude/settings.json (\"hooks\": { ... }):\n")
    print(json.dumps(HOOKS, indent=2))
    print("\n    UserPromptSubmit → injects verified memory (enforced).")
    print("    SessionEnd       → captures the session into memory.")
    print("\nThen restart Claude Code. Toggle anytime with /omni-memory on|off.")
    return 0


def _install_antigravity() -> int:
    print("[+] Antigravity: register OmniMemory as an MCP server (P1).")
    print("    command: omni-memory serve-mcp   (coming in P1)")
    print(f"    skill/config source: {SKILL_SRC}")
    return 0
