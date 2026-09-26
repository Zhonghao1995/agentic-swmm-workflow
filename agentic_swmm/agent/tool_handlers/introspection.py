"""Agent introspection handlers (PRD #128 — Phase 2 Group C, FINAL group).

Family: read-only introspection of the agent runtime itself.

* ``_doctor_tool`` — runs the built-in ``aiswmm doctor`` CLI through
  the shared subprocess wrapper. Surfaces environment / config /
  optional-extras diagnostics so the planner can quote them when a
  setup is broken.

The handler is a read against agent-side state (CLI doctor output);
it never mutates run evidence. The ``retrieve_memory`` retriever that
lived here shelled out to the swmm-rag-memory skill, retired with the
RAG corpus in the memory simplification (2026-09-26).

``_run_cli_tool`` comes from ``tool_handlers/_shared`` — the
cross-cutting helpers every family imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _run_cli_tool
from agentic_swmm.agent.types import ToolCall


def _doctor_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    return _run_cli_tool(call, session_dir, ["doctor"])


__all__ = [
    "_doctor_tool",
    "tool_specs",
]


def tool_specs():
    """This family's planner tools (issue #358 self-registration)."""
    from agentic_swmm.agent.tool_handlers._shared import _object
    from agentic_swmm.agent.types import ToolSpec

    return [
        ToolSpec("doctor", "Run the built-in Agentic SWMM runtime doctor.", _object({}), _doctor_tool),
    ]
