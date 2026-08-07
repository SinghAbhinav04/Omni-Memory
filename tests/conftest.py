"""Shared fixtures: a throwaway git repo with sample source + an open Store.

The sample repo exercises the code graph (inheritance, calls, raises, emits) so
extraction/build/staleness/dossier tests all have real structure to assert on.
Tests are backend-agnostic: they run on stdlib `ast` (Python) and, when
tree-sitter is installed, on that backend too.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SAMPLE_SVC = '''\
class Base:
    def run(self):
        return 1


class Service(Base):
    """A service that creates orders."""

    def create_order(self, user, items):
        """Create an order and publish an event. Raises ValidationError."""
        if not items:
            raise ValidationError("no items")
        row = self._insert(user, items)
        self._publish(row)
        return row

    def _insert(self, user, items):
        return {"id": 1}

    def _publish(self, row):
        kafka.publish("order.created", row)


def helper(x):
    return x + 1
'''


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo with the sample source committed and .omni-memory ignored."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / ".gitignore").write_text(".omni-memory/\n")
    (tmp_path / "svc.py").write_text(SAMPLE_SVC)
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


@pytest.fixture
def store(repo: Path):
    from omni_memory.store import Store
    return Store(repo)


@pytest.fixture(autouse=True)
def _clear_extract_cache():
    """The extract cache is process-global; reset it so per-test repos don't leak."""
    from omni_memory.graph import extract
    extract._EXTRACT_CACHE.clear()
    yield
    extract._EXTRACT_CACHE.clear()
