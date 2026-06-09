"""Experiment-audit handler (PRD #128 Phase 2 Group B).

Family: ``swmm-experiment-audit``.

Single-handler family extracted from ``tool_registry.py`` as part of
Phase 2 Group B of the registry split (Phase 1 — cross-cutting
helpers — landed in PR #209, see ``_shared.py``):

* :func:`_audit_run_tool` — runs the deterministic provenance /
  comparison / experiment-note pipeline over a run directory via the
  ``swmm-experiment-audit.audit_run`` MCP tool.

The handler is MCP-routed. See ``swmm_network.py`` for the rationale
behind the lazy-build / lazy-import pattern (avoids the
``tool_registry`` import cycle).

``_failure`` comes from ``tool_handlers/_shared`` — the cross-cutting
helpers every family imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.agent.tool_handlers._shared import _failure
from agentic_swmm.agent.types import ToolCall


def _audit_run_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``audit_run`` args to ``swmm-experiment-audit`` MCP schema."""

    run_dir = call.args.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir.strip():
        return _failure(call, "missing required argument: run_dir")
    args: dict[str, Any] = {"runDir": run_dir}
    if call.args.get("workflow_mode"):
        args["workflowMode"] = str(call.args["workflow_mode"])
    if call.args.get("objective"):
        args["objective"] = str(call.args["objective"])
    # C4 (issue #246): expose --compare-to so the agent can trigger
    # side-by-side comparison in a single audit_run call.
    if call.args.get("compare_to"):
        args["compareTo"] = str(call.args["compare_to"])
    return args


def _build_handler() -> Any:
    # Lazy import — see ``swmm_network`` module docstring.
    from agentic_swmm.agent.tool_registry import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-experiment-audit", "audit_run", args_mapper=_audit_run_args
    )


_audit_run_tool = _build_handler()


__all__ = [
    "_audit_run_args",
    "_audit_run_tool",
]
