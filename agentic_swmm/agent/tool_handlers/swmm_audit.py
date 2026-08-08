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

from agentic_swmm.agent.tool_handlers._shared import _failure, _resolve_run_dir
from agentic_swmm.agent.types import ToolCall, ToolSpec


def _audit_run_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``audit_run`` args to ``swmm-experiment-audit`` MCP schema."""

    # ADR-0004: resolve to an ABSOLUTE path before crossing the MCP
    # boundary. The Node server resolves relative paths against its own
    # cwd (mcp/swmm-experiment-audit/), which used to scatter 09_audit/
    # into the source tree. Same seam the plot mapper uses.
    run_dir = _resolve_run_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    args: dict[str, Any] = {"runDir": str(run_dir)}
    if call.args.get("workflow_mode"):
        args["workflowMode"] = str(call.args["workflow_mode"])
    if call.args.get("objective"):
        args["objective"] = str(call.args["objective"])
    # C4 (issue #246): expose --compare-to so the agent can trigger
    # side-by-side comparison in a single audit_run call.
    if call.args.get("compare_to"):
        args["compareTo"] = str(call.args["compare_to"])
    # Issue #328: the audit script's own default writes the modelling note
    # into the user's real Obsidian vault under $HOME. The CLI verb defaults
    # to --no-obsidian; the agent path now matches it. Vault mirroring is an
    # explicit opt-IN (obsidian=true), so a bare agent audit never has side
    # effects outside the run directory.
    if not call.args.get("obsidian"):
        args["noObsidian"] = True
    return args


def _build_handler() -> Any:
    # Lazy import — see ``swmm_network`` module docstring.
    from agentic_swmm.agent.tool_handlers._shared import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-experiment-audit", "audit_run", args_mapper=_audit_run_args
    )


_audit_run_tool = _build_handler()


__all__ = [
    "_audit_run_args",
    "_audit_run_tool",
    "tool_specs",
]


def tool_specs() -> list[ToolSpec]:
    """This family's planner tools (issue #358 self-registration)."""
    from agentic_swmm.agent.tool_handlers._shared import _object

    return [
        ToolSpec(
            "audit_run",
            "Audit a run directory and write deterministic provenance/comparison/note artifacts. Writes stay inside the run directory unless obsidian=true.",
            _object({"run_dir": {"type": "string"}, "workflow_mode": {"type": "string"}, "objective": {"type": "string"}, "compare_to": {"type": "string", "description": "Optional path to a second run directory; when present, writes comparison.json comparing the two runs."}, "obsidian": {"type": "boolean", "description": "Also mirror the modelling note into the user's local Obsidian vault (~/Documents/Agentic-SWMM-Obsidian-Vault). Default false: agent-path audits have no side effects outside the run directory, matching the CLI's --no-obsidian default (issue #328)."}}, ["run_dir"]),
            _audit_run_tool,
        ),
    ]
