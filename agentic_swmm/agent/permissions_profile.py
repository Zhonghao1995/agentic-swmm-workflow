"""Permission profile (``SAFE`` / ``QUICK``) for the agent executor.

QUICK is the default; SAFE is opt-in via ``--safe``.

PRD_runtime "Module: Permissions profile":

- ``Profile.QUICK`` (default): auto-approves any tool the registry
  classifies ``is_read_only`` (``read_file``, ``list_skills``,
  ``list_mcp_*``, ``inspect_plot_options``, ...). Write/subprocess
  tools still prompt.
- ``Profile.SAFE``: never auto-approves. Every tool call goes through
  ``permissions.prompt_user`` (which, in non-TTY contexts, short-circuits
  to allow — so tests and CI keep working). Opt in with ``--safe``.

The profile module is intentionally tiny so it can be unit-tested in
isolation without touching the registry's tool catalogue.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class _RegistryProto(Protocol):
    def is_read_only(self, name: str) -> bool: ...  # pragma: no cover - structural


class Profile(Enum):
    SAFE = "safe"
    QUICK = "quick"

    def auto_approve(self, tool_name: str, registry: _RegistryProto) -> bool:
        """Return ``True`` when the call may run without prompting."""
        if self is Profile.QUICK:
            return bool(registry.is_read_only(tool_name))
        return False


def profile_from_string(value: str | None) -> Profile:
    """Map a CLI/env string to a ``Profile``.

    Empty / missing / unknown values fall back to ``QUICK`` — the new
    default (see module docstring). ``"safe"`` (case-insensitive)
    explicitly selects ``SAFE``; ``"quick"`` is accepted for symmetry
    and for the hidden ``--quick`` CLI alias.
    """
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "safe":
            return Profile.SAFE
        if normalized == "quick":
            return Profile.QUICK
    return Profile.QUICK
