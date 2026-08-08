"""Model layer — turns code/docs/transcripts into structured memory.

Provider auto-detected from env (zero extra deps — uses urllib):
  GEMINI_API_KEY / GOOGLE_API_KEY → Gemini (default; model GEMINI_MODEL)
  OPENAI_API_KEY                  → OpenAI-compatible (OPENAI_MODEL, OPENAI_BASE_URL)
  ANTHROPIC_API_KEY               → Anthropic (ANTHROPIC_MODEL)
No key → callers fall back to the heuristic extractor. Nothing is required.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

CREDS = Path.home() / ".omni-memory" / "credentials.json"


def _key(name: str) -> str | None:
    """Look up a key from env first, then the chmod-600 credentials file
    (~/.omni-memory/credentials.json) — never from the repo."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        return json.loads(CREDS.read_text(encoding="utf-8")).get(name)
    except Exception:  # noqa: BLE001
        return None


def _agent_cmd() -> list[str] | None:
    """The coding agent's own CLI, so extraction runs INSIDE Claude Code /
    Antigravity (your subscription) with no API key."""
    override = os.environ.get("OMNI_AGENT_CMD")
    if override:
        return override.split()
    if shutil.which("claude"):
        return ["claude", "-p"]
    return None


def provider() -> str | None:
    """Priority: explicit OMNI_LLM → the coding agent's own CLI (Claude Code /
    Antigravity — no key) → an API key only if NO agent is available.

    So inside Claude Code / Antigravity it always runs in the agent; an API key
    is a pure fallback for headless/CI environments with no agent CLI.
    """
    forced = os.environ.get("OMNI_LLM")
    if forced:
        return forced
    if _agent_cmd():
        return "agent"
    if _key("GEMINI_API_KEY") or _key("GOOGLE_API_KEY"):
        return "gemini"
    if _key("ANTHROPIC_API_KEY"):
        return "anthropic"
    if _key("OPENAI_API_KEY"):
        return "openai"
    return None


def available() -> bool:
    return provider() is not None


def _post(url: str, headers: dict, payload: dict, timeout: int = 120) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def complete(system: str, user: str) -> str:
    p = provider()
    if p == "agent":                       # run inside Claude Code / Antigravity CLI
        cmd = _agent_cmd()
        r = subprocess.run(cmd, input=system + "\n\n" + user,
                           capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise RuntimeError(f"agent CLI failed: {r.stderr[:200]}")
        return r.stdout
    if p == "gemini":
        key = _key("GEMINI_API_KEY") or _key("GOOGLE_API_KEY")
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        d = _post(url, {}, {"contents": [{"parts": [{"text": system + "\n\n" + user}]}],
                            "generationConfig": {"temperature": 0.2}})
        return d["candidates"][0]["content"]["parts"][0]["text"]
    if p == "openai":
        key = _key("OPENAI_API_KEY")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        d = _post(f"{base}/chat/completions", {"Authorization": f"Bearer {key}"},
                  {"model": model, "temperature": 0.2, "messages": [
                      {"role": "system", "content": system},
                      {"role": "user", "content": user}]})
        return d["choices"][0]["message"]["content"]
    if p == "anthropic":
        key = _key("ANTHROPIC_API_KEY")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        d = _post("https://api.anthropic.com/v1/messages",
                  {"x-api-key": key, "anthropic-version": "2023-06-01"},
                  {"model": model, "max_tokens": 8192, "system": system,
                   "messages": [{"role": "user", "content": user}]})
        return d["content"][0]["text"]
    raise RuntimeError("no LLM provider configured (set GEMINI_API_KEY)")


def extract_memories(prompt: str, content: str) -> list[dict]:
    """Run a prompt over content, parse the JSON array of memory items."""
    txt = complete(prompt, content)
    return parse_json_array(txt)


def parse_json_array(txt: str) -> list[dict]:
    txt = txt.strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-z]*\n?|\n?```$", "", txt.strip())
    m = re.search(r"\[.*\]", txt, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return [x for x in arr if isinstance(x, dict) and x.get("text")]
    except Exception:  # noqa: BLE001
        return []
