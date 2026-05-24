from __future__ import annotations

from pathlib import Path

DEFAULT_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "coverage",
}


def iter_files(root: Path, *, max_files: int = 5000) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if len(files) >= max_files:
            break
        if any(part in DEFAULT_IGNORES for part in path.relative_to(root).parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def rel_id(prefix: str, root: Path, path: Path) -> str:
    rel = path.relative_to(root).as_posix()
    return f"{prefix}:{rel}" if rel else prefix


def read_text(path: Path, *, limit: int = 1_000_000) -> str:
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

