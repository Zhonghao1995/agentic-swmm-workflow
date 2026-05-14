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

    The agent always called tools auto-approve before the Runtime UX
    PRD; this function is the new opt-in confirmation seam. To preserve
    test and CI behaviour (where stdin is not a TTY), non-interactive
    callers receive an automatic ``True``. Interactive shells get a
    minimal y/N prompt. Setting ``AISWMM_AUTO_APPROVE=1`` skips the
    prompt entirely (handy for ``--quick`` runs piped through scripts).
    """
    if os.environ.get("AISWMM_AUTO_APPROVE") == "1":
        return True
    if not sys.stdin.isatty():
        return True
    try:
        answer = input(f"Run {tool_name}? [Y/n] ").strip().lower()
    except EOFError:
        return True
    return answer in {"", "y", "yes"}
