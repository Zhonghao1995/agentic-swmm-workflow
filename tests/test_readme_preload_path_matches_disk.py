"""Regression test for issue #123.

Asserts that every path declared in the README's "preload" code block and
in the swmm-end-to-end SKILL's preload reference resolves to a real file
or directory under the repo root.

Rationale: A new aiswmm user follows the README literally with
``ls agent/memory/``. If the documented preload path disagrees with the
on-disk layout, the very first command after ``cd`` errors. This guard
catches that class of drift early.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
SKILL = REPO_ROOT / "skills" / "swmm-end-to-end" / "SKILL.md"


def _extract_preload_paths(markdown_path: Path) -> list[str]:
    """Return path strings from any fenced ``text`` code block that
    appears within a few lines of the literal word "preload"."""
    text = markdown_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    paths: list[str] = []
    inside_block = False
    near_preload = False
    fence_pattern = re.compile(r"^```(\w*)\s*$")
    for idx, line in enumerate(lines):
        if not inside_block:
            m = fence_pattern.match(line)
            if m:
                lang = m.group(1).lower()
                # Look backwards a few lines for "preload"
                window = "\n".join(lines[max(0, idx - 6) : idx]).lower()
                if "preload" in window and lang in ("text", "bash", "sh", "shell", ""):
                    inside_block = True
                    near_preload = True
            continue
        # Inside a fenced block
        if fence_pattern.match(line):
            inside_block = False
            near_preload = False
            continue
        candidate = line.strip()
        if not candidate:
            continue
        # Skip comments
        if candidate.startswith("#"):
            continue
        # Skip shell prompt lines
        if candidate.startswith("$"):
            continue
        # Accept any token that contains a slash
        if "/" in candidate:
            paths.append(candidate)
    return paths


def _readme_preload_paths() -> list[str]:
    return _extract_preload_paths(README)


SKILL_MEMORY_FILES = [
    "identification_memory.md",
    "soul.md",
    "operational_memory.md",
    "modeling_workflow_memory.md",
    "evidence_memory.md",
    "user_bridge_memory.md",
]


def _skill_preload_base() -> str:
    """Return the directory the SKILL says to load the memory files from.

    Parse the narrative line:
    ``load the Markdown files in `agent/something/```. Whatever the
    backticked path is, the named ``SKILL_MEMORY_FILES`` must resolve
    underneath it.
    """
    text = SKILL.read_text(encoding="utf-8")
    pattern = re.compile(
        r"load the Markdown files in `([A-Za-z0-9_./-]+/)`",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    assert match is not None, (
        "swmm-end-to-end SKILL no longer contains the 'load the Markdown "
        "files in `<path>/`' preload instruction. Update this test if the "
        "preload contract changed."
    )
    return match.group(1)


@pytest.mark.parametrize("path", _readme_preload_paths())
def test_readme_preload_path_exists_on_disk(path: str) -> None:
    resolved = REPO_ROOT / path
    assert resolved.exists(), (
        f"README preload references '{path}' but no such file or "
        f"directory exists at {resolved}"
    )


@pytest.mark.parametrize("memory_file", SKILL_MEMORY_FILES)
def test_swmm_end_to_end_skill_preload_files_exist_under_documented_dir(
    memory_file: str,
) -> None:
    """Each named memory file in the SKILL's preload list must exist at
    the directory the SKILL points to. Catches drift between the SKILL's
    "load the Markdown files in `agent/...`" instruction and where the
    files actually live."""
    base = _skill_preload_base()
    resolved = REPO_ROOT / base / memory_file
    assert resolved.exists(), (
        f"swmm-end-to-end SKILL says to preload '{memory_file}' from "
        f"'{base}', but {resolved} does not exist on disk"
    )
