"""Decide whether the planner should re-introspect skills / MCP this turn.

PRD_runtime "Module: PlannerIntrospection":

- ``should_introspect(session_state, prompt) -> (skip_skills, skip_mcp)``
- ``skip_skills = True`` when the session already contains a
  ``list_skills`` call.
- ``skip_mcp = True`` when the session already contains
  ``list_mcp_servers`` **and** at least one ``list_mcp_tools`` call.

The function is pure — no I/O, no globals. It accepts either schema
field: ``session_state["plan"]`` (as the PRD names it) or
``session_state["tool_history"]`` (the on-disk schema). The two are
shape-equivalent: a list of ``{"tool": str, "args": dict}`` records.
"""

from __future__ import annotations

from typing import Any, Iterable


def should_introspect(session_state: dict[str, Any], prompt: str) -> tuple[bool, bool]:
    """Return ``(skip_skills, skip_mcp)`` for the upcoming planner turn.

    The ``prompt`` argument is accepted for forward compatibility (the
    PRD signature requires it) but is not yet consulted; introspection
    skipping is driven purely by what the session already inspected.
    """
    del prompt  # currently unused — see docstring

    history = _coerce_history(session_state)
    tool_names = [str(entry.get("tool")) for entry in history if isinstance(entry, dict)]

    skip_skills = "list_skills" in tool_names

    has_server_list = "list_mcp_servers" in tool_names
    has_tool_list = "list_mcp_tools" in tool_names
    skip_mcp = has_server_list and has_tool_list

    return skip_skills, skip_mcp


def _coerce_history(session_state: dict[str, Any]) -> Iterable[dict[str, Any]]:
    if not isinstance(session_state, dict):
        return []
    for key in ("tool_history", "plan"):
        value = session_state.get(key)
        if isinstance(value, list) and value:
            return value
    return []
