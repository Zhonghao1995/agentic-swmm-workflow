from __future__ import annotations

from pathlib import Path

from agentic_swmm.utils.paths import repo_root


BLOCKED_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
BLOCKED_FILENAMES = {".env", "config.toml"}
ALLOWED_COMMANDS = {
    "pytest",
    "python_module_cli",
    "node_script",
    "swmm5",
}


def repo_relative_path(value: str) -> Path | None:
    raw = Path(value).expanduser()
    candidate = raw.resolve() if raw.is_absolute() else (repo_root() / raw).resolve()
    try:
        candidate.relative_to(repo_root().resolve())
    except ValueError:
        return None
    return candidate


def is_allowed_write_path(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return False
    if any(part in BLOCKED_PARTS for part in relative.parts):
        return False
    if path.name in BLOCKED_FILENAMES:
        return False
    return True


def is_evidence_path(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(repo_root().resolve())
    except ValueError:
        return False
    return relative.parts[:1] == ("runs",) or relative.parts[:2] == ("memory", "modeling-memory")
