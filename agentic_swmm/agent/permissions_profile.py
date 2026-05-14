"""Permission profile (``SAFE`` / ``QUICK``) for the agent executor.

PRD_runtime "Module: Permissions profile":

- ``Profile.SAFE`` (default): never auto-approves. Every tool call goes
  through ``permissions.prompt_user`` (which, in non-TTY contexts,
  short-circuits to allow — so tests and CI keep working).
- ``Profile.QUICK``: auto-approves any tool the registry classifies
  ``is_read_only``. Write/subprocess tools still prompt.

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
    """Map a CLI/env string to a ``Profile``. Unknown values fall back
    to ``SAFE`` — the fail-safe default."""
    if isinstance(value, str) and value.strip().lower() == "quick":
        return Profile.QUICK
    return Profile.SAFE
