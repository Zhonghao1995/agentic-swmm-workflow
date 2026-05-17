"""Demo acceptance handler (PRD #128).

Single-tool family — kept as its own module to demonstrate the
one-skill-one-handler-module precedent that ``tool_registry.py``
registers via ``_build_tools``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.types import ToolCall


def _demo_acceptance_tool(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    # Late import: ``_run_cli_tool`` lives in ``tool_registry`` until that
    # split lands. Importing here keeps the edge one-way (handlers ->
    # registry helpers) and avoids the circular dependency that would
    # arise from a top-level import.
    from agentic_swmm.agent.tool_registry import _run_cli_tool

    command = ["demo", "acceptance", "--run-id", str(call.args.get("run_id", "agent-latest"))]
    if call.args.get("keep_existing"):
        command.append("--keep-existing")
    return _run_cli_tool(call, session_dir, command)


__all__ = ["_demo_acceptance_tool"]
