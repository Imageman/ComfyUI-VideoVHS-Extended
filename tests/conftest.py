"""Classify existing unittest checks without importing ComfyUI runtime."""
from __future__ import annotations

from typing import Any
import pytest


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Keep legacy full-corpus checks out of the fast unit selection."""
    for item in items:
        if getattr(getattr(item, "cls", None), "__name__", "") == "YuE2AutoInstrumentalTests":
            marker = "corpus" if "real_corpus" in item.name else "unit"
            item.add_marker(getattr(pytest.mark, marker))


def pytest_collect_directory(path: Any, parent: Any) -> Any:
    """Collect the fixture directory without importing the node package."""
    from pathlib import Path
    if path == Path(__file__).resolve().parent:
        return pytest.Dir.from_parent(parent, path=path)
    return None


def pytest_addoption(parser: Any) -> None:
    """Allow a deterministic representative extended run for local and fast CI review."""
    parser.addoption("--representative", action="store_true", default=False)


def pytest_collection_finish(session: Any) -> None:
    """Limit only extended source parameters when the explicit sample flag is set."""
    if not session.config.getoption("--representative"):
        return
    from yue2_test_support import verify_manifest
    from yue2_auto_instrumental import ABCScoreParser, ScoreError
    selected = []
    for path in verify_manifest():
        try:
            ABCScoreParser("strict", 4).parse(path.read_text(encoding="utf-8"))
        except ScoreError:
            continue
        selected.append(path.name)
        if len(selected) == 25:
            break
    kept, removed = [], []
    for item in session.items:
        path = getattr(getattr(item, "callspec", None), "params", {}).get("path")
        is_extended = item.get_closest_marker("extended") is not None
        (removed if is_extended and path is not None and path.name not in selected else kept).append(item)
    session.items[:] = kept
    session.config.hook.pytest_deselected(items=removed)
