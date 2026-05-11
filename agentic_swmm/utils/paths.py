from __future__ import annotations

import sysconfig
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def packaged_resource_root() -> Path:
    return Path(sysconfig.get_path("data")) / "agentic-swmm-workflow"


def resource_path(*parts: str) -> Path:
    source_path = repo_root().joinpath(*parts)
    if source_path.exists():
        return source_path

    installed_path = packaged_resource_root().joinpath(*parts)
    if installed_path.exists():
        return installed_path

    raise FileNotFoundError(
        "Required Agentic SWMM resource is missing. Checked source path "
        f"{source_path} and installed package path {installed_path}."
    )


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
    return resource_path(*parts)
