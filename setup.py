from __future__ import annotations

import os
import subprocess
from pathlib import Path

from setuptools import setup


PUBLIC_RESOURCE_DIRS = (
    "agent",
    "examples",
    "integrations",
    "mcp",
    "scripts",
    "skills",
    "web",
)

PRIVATE_RESOURCE_DIRS = PUBLIC_RESOURCE_DIRS + (
    "data",
    "memory",
)

PUBLIC_SKILLS = {
    "swmm-builder",
    "swmm-calibration",
    "swmm-climate",
    "swmm-end-to-end",
    "swmm-experiment-audit",
    "swmm-gis",
    "swmm-modeling-memory",
    "swmm-network",
    "swmm-params",
    "swmm-plot",
    "swmm-rag-memory",
    "swmm-runner",
    "swmm-uncertainty",
}

PUBLIC_AGENT_FILES = {
    Path("agent/config/intent_map.json"),
}

EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    "generated",
    "node_modules",
    "runs",
}

EXCLUDED_FILES = {
    ".DS_Store",
}


def collect_resource_files() -> list[tuple[str, list[str]]]:
    root = Path(__file__).parent
    tracked = tracked_resource_files(root)
    data_files: list[tuple[str, list[str]]] = []
    files_by_target: dict[str, list[str]] = {}
    for path in tracked:
        relative_parent = path.parent
        target = str(Path("aiswmm") / relative_parent)
        files_by_target.setdefault(target, []).append(path.as_posix())
    data_files.extend((target, sorted(files)) for target, files in sorted(files_by_target.items()))
    return data_files


def tracked_resource_files(root: Path) -> list[Path]:
    profile = package_profile()
    resource_dirs = PRIVATE_RESOURCE_DIRS if profile == "private" else PUBLIC_RESOURCE_DIRS
    git_dir = root / ".git"
    if git_dir.exists():
        proc = subprocess.run(
            ["git", "ls-files", *resource_dirs],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [
            Path(line)
            for line in proc.stdout.splitlines()
            if line and _include_resource_path(root, Path(line), profile=profile)
        ]

    files: list[Path] = []
    for resource_dir in resource_dirs:
        base = root / resource_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(root)
            if _include_resource_path(root, relative, profile=profile):
                files.append(relative)
    return sorted(files)


def package_profile() -> str:
    profile = os.environ.get("AISWMM_PACKAGE_PROFILE", "public").strip().lower()
    if profile not in {"public", "private"}:
        raise ValueError("AISWMM_PACKAGE_PROFILE must be 'public' or 'private'")
    return profile


def _include_resource_path(root: Path, relative: Path, *, profile: str) -> bool:
    path = root / relative
    if not path.is_file():
        return False
    if relative.name in EXCLUDED_FILES:
        return False
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        return False
    if profile == "private":
        return True
    return _include_public_resource(relative)


def _include_public_resource(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return False
    if parts[0] == "agent":
        return relative in PUBLIC_AGENT_FILES
    if parts[0] == "skills":
        return len(parts) >= 2 and parts[1] in PUBLIC_SKILLS
    if parts[0] == "scripts":
        return len(parts) == 2 or (len(parts) >= 2 and parts[1] == "acceptance")
    if parts[0] in {"data", "memory"}:
        return False
    return parts[0] in {"examples", "integrations", "mcp", "web"}


setup(data_files=collect_resource_files())
