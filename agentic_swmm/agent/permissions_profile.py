"""Permission profile (``SAFE`` / ``QUICK``) for the agent executor.

QUICK is the default; SAFE is opt-in via ``--safe``.

PRD_runtime "Module: Permissions profile":

- ``Profile.QUICK`` (default): auto-approves any tool the registry
  classifies ``is_read_only`` (``read_file``, ``list_skills``,
  ``list_mcp_*``, ``inspect_plot_options``, ...). Write/subprocess
  tools still prompt.
- ``Profile.SAFE``: prompts for every tool that touches the user's
  files, network or run, and only skips the runtime's own introspection
  (``INTROSPECTION_TOOLS``: the skill and MCP catalogues). Opt in with
  ``--safe``. Live finding F-48 (2026-09-02): before this exception the
  cautious profile asked eight questions (list_skills, list_mcp_servers,
  four list_mcp_tools, select_skill, read_skill) before the user's
  request even started, and only three of a turn's sixteen questions
  guarded a side effect.

The profile module is intentionally tiny so it can be unit-tested in
isolation without touching the registry's tool catalogue.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class _RegistryProto(Protocol):
    def is_read_only(self, name: str) -> bool: ...  # pragma: no cover - structural


# Tools that look at the product itself (its skill catalogue, its MCP
# catalogue, its own capabilities) rather than at anything of the user's.
# Even the cautious profile does not ask before the runtime reads its own
# table of contents. ``list_dir``/``read_file``/``search_files`` are NOT
# here: they read the user's filesystem, which is exactly what SAFE exists
# to put a question in front of.
INTROSPECTION_TOOLS = frozenset(
    {"capabilities", "list_skills", "list_mcp_servers", "list_mcp_tools", "select_skill", "read_skill"}
)


class Profile(Enum):
    SAFE = "safe"
    QUICK = "quick"

    def auto_approve(self, tool_name: str, registry: _RegistryProto) -> bool:
        """Return ``True`` when the call may run without prompting."""
        if not registry.is_read_only(tool_name):
            return False
        if self is Profile.QUICK:
            return True
        return tool_name in INTROSPECTION_TOOLS


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
