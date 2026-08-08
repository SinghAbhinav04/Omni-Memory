"""Assemble a compact, high-signal repo snapshot to feed the model on `build`.

Prioritizes the files that reveal architecture: manifests, docs, entrypoints,
controllers/routes/services/models/config. Truncated to a char budget (a later
phase adds per-area chunking via the AST code graph for big monorepos).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_PRIORITY = (
    "readme", "package.json", "pom.xml", "build.gradle", "requirements",
    "pyproject", "go.mod", "cargo.toml", "dockerfile", "docker-compose",
    "application.yml", "application.properties", ".env.example",
)
_DIR_HINTS = ("controller", "route", "service", "handler", "model", "entity",
              "repository", "schema", "config", "kafka", "consumer", "producer",
              "api", "endpoint", "main", "app", "index")
_CODE_EXT = (".py", ".ts", ".js", ".java", ".go", ".rb", ".kt", ".rs", ".md",
             ".yml", ".yaml", ".sql")


def _tracked_files(root: Path) -> list[str]:
    try:
        r = subprocess.run(["git", "-C", str(root), "ls-files"],
                           capture_output=True, text=True, timeout=20,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return [f for f in r.stdout.splitlines() if f]
    except Exception:  # noqa: BLE001
        pass
    return [str(p.relative_to(root)) for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts][:5000]


def _score(path: str) -> int:
    low = path.lower()
    s = 0
    if any(k in low for k in _PRIORITY):
        s += 100
    if low.endswith(".md"):
        s += 40
    if any(h in low for h in _DIR_HINTS):
        s += 20
    if low.endswith(_CODE_EXT):
        s += 5
    s -= low.count("/") * 2          # prefer shallower files
    return s


def gather(root: Path, max_chars: int = 140_000, per_file: int = 6_000) -> str:
    files = _tracked_files(root)
    tree = "\n".join(files[:1000])
    parts = [f"# REPO: {root.name}\n# FILE TREE ({len(files)} files)\n{tree}\n"]
    budget = max_chars - len(parts[0])
    for f in sorted(files, key=_score, reverse=True):
        if budget <= 0:
            break
        if not f.lower().endswith(_CODE_EXT):
            continue
        try:
            text = (root / f).read_text(encoding="utf-8", errors="ignore")[:per_file]
        except Exception:  # noqa: BLE001
            continue
        chunk = f"\n\n# FILE: {f}\n{text}"
        if len(chunk) > budget:
            chunk = chunk[:budget]
        parts.append(chunk)
        budget -= len(chunk)
    return "".join(parts)
