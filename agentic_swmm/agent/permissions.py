from __future__ import annotations

import os
import sys
from dataclasses import dataclass
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


AUTO_APPROVE_ENV = "AISWMM_AUTO_APPROVE"

HEADLESS_DENIAL_HINT = (
    f"No terminal was attached, so the call was refused rather than run "
    f"unattended. For trusted automation, set {AUTO_APPROVE_ENV}=1."
)


@dataclass(frozen=True)
class ApprovalDecision:
    """Whether a tool may run, and why that was decided.

    ``reason`` is one of:

    * ``"auto"`` — trusted automation opted in via the env var;
    * ``"granted"`` — a human said yes;
    * ``"declined"`` — a human said no;
    * ``"headless"`` — there was no human to ask.

    The last two both deny, but only one of them is worth explaining: a
    user who typed ``n`` does not need a lecture, while a CI run needs to
    be told the opt-in exists.
    """

    approved: bool
    reason: str

    @property
    def needs_guidance(self) -> bool:
        """True when the caller should surface :data:`HEADLESS_DENIAL_HINT`."""
        return self.reason == "headless"


def request_approval(tool_name: str) -> ApprovalDecision:
    """Decide whether ``tool_name`` may execute, and record why.

    This is the opt-in confirmation seam. Interactive shells get a
    minimal y/N prompt. When there is no human on the other end (stdin is
    not a TTY, or the prompt hits EOF) the call FAILS CLOSED — a
    side-effecting tool is denied rather than silently auto-approved
    (review P1-2), because a headless / CI / background / injection-driven
    run has no one to catch a bad call. Trusted automation opts back into
    auto-approval explicitly with ``AISWMM_AUTO_APPROVE=1``.

    The decision carries its reason so the denial the user sees can name
    the cause and the opt-in instead of a bare refusal.
    """
    if os.environ.get(AUTO_APPROVE_ENV) == "1":
        return ApprovalDecision(approved=True, reason="auto")
    if not sys.stdin.isatty():
        return ApprovalDecision(approved=False, reason="headless")
    try:
        answer = input(f"Run {tool_name}? [Y/n] ").strip().lower()
    except EOFError:
        return ApprovalDecision(approved=False, reason="headless")
    if answer in {"", "y", "yes"}:
        return ApprovalDecision(approved=True, reason="granted")
    return ApprovalDecision(approved=False, reason="declined")


def prompt_user(tool_name: str) -> bool:
    """Return whether ``tool_name`` may execute.

    Thin bool view of :func:`request_approval`, kept because callers that
    only need the verdict (and the tests that patch this seam) read more
    plainly without unpacking a decision object.
    """
    return request_approval(tool_name).approved
