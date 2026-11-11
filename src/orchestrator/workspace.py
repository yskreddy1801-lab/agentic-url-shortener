"""Run workspace copies. Scenario patches are applied here, not in the main tree."""

from __future__ import annotations

import shutil
from pathlib import Path

_SKIP = {
    ".git",
    ".venv",
    "venv",
    "artifacts",
    "data",
    "__pycache__",
    ".pytest_cache",
    "uploads",
    ".mypy_cache",
    ".ruff_cache",
}


def create_workspace(repo_root: Path, run_id: str) -> Path:
    destination = repo_root / "artifacts" / "workspaces" / run_id
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo_root, destination, ignore=_ignore)
    return destination


def apply_files(root: Path, files: list[dict]) -> list[dict]:
    snapshot = []
    for item in files:
        path = root / item["path"]
        existed = path.is_file()
        previous = path.read_text(encoding="utf-8") if existed else None
        snapshot.append({"path": item["path"], "existed": existed, "previous": previous})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")
    return snapshot


def rollback_files(root: Path, snapshot: list[dict]) -> None:
    for item in reversed(snapshot):
        path = root / item["path"]
        if item["existed"]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["previous"], encoding="utf-8")
        elif path.exists():
            path.unlink()


def _ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in _SKIP or name.endswith(".pyc")}
