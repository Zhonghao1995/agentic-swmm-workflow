from __future__ import annotations

import os
import sys
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


def prompt_user(tool_name: str) -> bool:
    """Ask the user whether to execute ``tool_name``.

    This is the opt-in confirmation seam. Interactive shells get a
    minimal y/N prompt. When there is no human on the other end (stdin is
    not a TTY, or the prompt hits EOF) the call FAILS CLOSED — a
    side-effecting tool is denied rather than silently auto-approved
    (review P1-2), because a headless / CI / background / injection-driven
    run has no one to catch a bad call. Trusted automation opts back into
    auto-approval explicitly with ``AISWMM_AUTO_APPROVE=1``.
    """
    if os.environ.get("AISWMM_AUTO_APPROVE") == "1":
        return True
    if not sys.stdin.isatty():
        return False
    try:
        answer = input(f"Run {tool_name}? [Y/n] ").strip().lower()
    except EOFError:
        return False
    return answer in {"", "y", "yes"}
