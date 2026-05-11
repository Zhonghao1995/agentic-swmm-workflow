from __future__ import annotations

import subprocess
from pathlib import Path

from setuptools import setup


RESOURCE_DIRS = (
    "agentic-ai",
    "examples",
    "integrations",
    "scripts",
    "skills",
)

EXCLUDED_DIRS = {
    "__pycache__",
    ".pytest_cache",
    "generated",
    "node_modules",
}


def collect_resource_files() -> list[tuple[str, list[str]]]:
    root = Path(__file__).parent
    tracked = tracked_resource_files(root)
    data_files: list[tuple[str, list[str]]] = []
    files_by_target: dict[str, list[str]] = {}
    for path in tracked:
        relative_parent = path.parent
        target = str(Path("agentic-swmm-workflow") / relative_parent)
        files_by_target.setdefault(target, []).append(path.as_posix())
    data_files.extend((target, sorted(files)) for target, files in sorted(files_by_target.items()))
    return data_files


def tracked_resource_files(root: Path) -> list[Path]:
    git_dir = root / ".git"
    if git_dir.exists():
        proc = subprocess.run(
            ["git", "ls-files", *RESOURCE_DIRS],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [
            Path(line)
            for line in proc.stdout.splitlines()
            if line and (root / line).is_file() and not any(part in EXCLUDED_DIRS for part in Path(line).parts)
        ]

    files: list[Path] = []
    for resource_dir in RESOURCE_DIRS:
        base = root / resource_dir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and not any(part in EXCLUDED_DIRS for part in path.parts):
                files.append(path.relative_to(root))
    return sorted(files)


setup(data_files=collect_resource_files())
