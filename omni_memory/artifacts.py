"""AI-written doc artifacts — api-map.md and linkup.md.

Modeled on the hand-written work/docs kb: an endpoint→service→repo API map and a
master cross-reference (Mermaid dependency graph + controller/service/repo maps +
Kafka contracts + shared DBs/code). These regenerate from the live codebase so
they never drift.
"""
from __future__ import annotations

from pathlib import Path

from . import context, llm
from .store import Store

APIMAP_PROMPT = """\
You are OmniMemory generating `api-map.md` for this repository. Output ONLY
GitHub-flavored Markdown (no preamble). Structure:
- A short header: base path / auth headers if evident.
- One section per controller/route group, each with a table:
  | Method | Path | Request (DTO/params) | Flow: Controller -> Service -> Repository/downstream |
Be specific: real controller/service/repo class names, real paths, real DTOs,
real downstreams (DB, Kafka topic, external API). Note Kafka publishes as
`-> Kafka <topic>`. If a detail is inferred, append `(inferred)`. Base it strictly
on the code below — do not invent endpoints.
"""

LINKUP_PROMPT = """\
You are OmniMemory generating `linkup.md`, the master cross-reference. Output ONLY
Markdown. Include, in order:
1. `## Dependency graph` — a Mermaid ```mermaid graph LR``` of repos/services/DBs/
   queues and how they call each other (REST, Kafka topics, shared DB).
2. `## Controller -> Service -> Repository` — a table of the main call chains.
3. `## Event contracts` — Kafka topics with producer(s) and consumer(s).
4. `## Shared data & code` — shared DBs/collections and shared utility classes.
Use real names from the code below. Mark inferred items `(inferred)`. No preamble.
"""


def _generate(root: Path, prompt: str) -> str:
    ctx = context.gather(root)
    return llm.complete(prompt, ctx).strip()


def generate_apimap(store: Store, root: Path) -> Path:
    out = store.dir / "api-map.md"
    out.write_text(_generate(root, APIMAP_PROMPT) + "\n", encoding="utf-8")
    return out


def generate_linkup(store: Store, root: Path) -> Path:
    out = store.dir / "linkup.md"
    out.write_text(_generate(root, LINKUP_PROMPT) + "\n", encoding="utf-8")
    return out


def generate_all(store: Store, root: Path) -> list[Path]:
    return [generate_apimap(store, root), generate_linkup(store, root)]
