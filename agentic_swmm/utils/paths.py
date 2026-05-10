from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def require_dir(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise FileNotFoundError(f"{label} does not exist or is not a directory: {resolved}")
    return resolved


def script_path(*parts: str) -> Path:
    path = repo_root().joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(f"Required repository script is missing: {path}")
    return path
