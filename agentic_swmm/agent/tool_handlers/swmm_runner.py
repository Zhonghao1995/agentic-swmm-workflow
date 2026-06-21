"""SWMM-runner handlers (PRD #128 Phase 2 Group A).

Family: ``swmm-runner``.

Tools that invoke the EPA SWMM5 engine via the
``mcp/swmm-runner`` MCP server. Phase 1 (#209) extracted cross-cutting
helpers to ``tool_handlers/_shared``; this module is Phase 2's Group A
slice for the runner family: it owns the ``run_swmm_inp`` args mapper
and the factory-built handler that pairs it with
``_make_mcp_routed_handler``.

``_make_mcp_routed_handler`` and the cross-family path/INP helpers
(``_resolve_inp_for_run``, ``_node_suggestions``) still live in
``tool_registry`` (deferred per #211 and reused across other groups).
They are imported lazily inside the body of ``_run_swmm_inp_args``
and ``_build_run_swmm_inp_tool`` to break a load-time circular import
— ``tool_registry`` imports this module near the end of its own load,
so by the time these handlers actually run, every symbol they need is
bound on ``tool_registry``.

Cross-cutting helpers (``_safe_name``, ``_resolve_or_create_run_dir``)
come from ``tool_handlers/_shared``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from agentic_swmm.agent.swmm_runtime.preflight import preflight_inp
from agentic_swmm.agent.tool_handlers._shared import (
    _failure,
    _resolve_or_create_run_dir,
    _safe_name,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

_log = logging.getLogger(__name__)

# User-only escape hatch (env, never an LLM-settable arg) to bypass the
# preflight gate. Honoured truthy values mirror the project's other env flags.
_SKIP_PREFLIGHT_VALUES = {"1", "true", "yes", "on"}


def _preflight_gate(call: ToolCall, inp: Path) -> dict[str, Any] | None:
    """Validate ``inp`` before the (minutes-long) SWMM run.

    Returns a fail-soft ``_failure`` payload to BLOCK the run on a preflight
    FAIL — the actionable detail goes in ``summary`` (the field the planner
    sees) so the LLM can fix the .inp and retry; repeated FAILs trip the
    planner's same-tool circuit breaker. Returns ``None`` to proceed: a WARN
    is logged (advisory, non-blocking), a PASS is silent. A user-only
    ``AISWMM_SKIP_PREFLIGHT`` env flag bypasses the gate entirely.

    No auto-fix by design: the modeler/LLM must change the model visibly
    (verification-first), so the gate never edits the .inp.
    """
    if os.environ.get("AISWMM_SKIP_PREFLIGHT", "").strip().lower() in _SKIP_PREFLIGHT_VALUES:
        return None
    report = preflight_inp(inp)
    if report.status == "FAIL":
        details = "; ".join(
            str(f.get("detail") or f.get("code")) for f in report.failures
        ) or "invalid .inp"
        return _failure(
            call,
            f"preflight blocked run_swmm_inp: {details} "
            "(fix the .inp and retry, or set AISWMM_SKIP_PREFLIGHT=1 to override)",
        )
    if report.warnings:
        _log.warning(
            "preflight warnings for %s: %s",
            inp,
            "; ".join(
                str(w.get("detail") or w.get("code")) for w in report.warnings
            ),
        )
    return None


def _run_swmm_inp_args(call: ToolCall, session_dir: Path) -> dict[str, Any]:
    """Map ``run_swmm_inp`` args to ``swmm-runner.swmm_run`` MCP schema.

    Path validation (in-repo + suffix) and default ``run_dir`` / ``node``
    selection mirror the historical in-process handler so behaviour is
    identical for the caller.
    """
    # Lazy import — see module docstring on the circular-load reasoning.
    from agentic_swmm.agent.tool_registry import (
        _node_suggestions,
        _resolve_inp_for_run,
    )

    inp = _resolve_inp_for_run(call)
    if isinstance(inp, dict):
        return inp
    blocked = _preflight_gate(call, inp)
    if blocked is not None:
        return blocked
    run_dir = _resolve_or_create_run_dir(call, "run_dir")
    if isinstance(run_dir, dict):
        return run_dir
    if run_dir is None:
        run_id = str(call.args.get("run_id") or f"{_safe_name(inp.stem)}-{int(time.time())}")
        run_dir = repo_root() / "runs" / "agent" / _safe_name(run_id)
    default_node = _node_suggestions(str(inp), limit=1)
    node = str(call.args.get("node") or (default_node[0] if default_node else "O1"))
    return {"inp": str(inp), "runDir": str(run_dir), "node": node}


def _build_run_swmm_inp_tool() -> Any:
    """Construct the ``run_swmm_inp`` ToolSpec handler.

    Late-imports ``_make_mcp_routed_handler`` from ``tool_registry`` —
    see module docstring on why module-level imports of that symbol
    are not safe here.
    """
    from agentic_swmm.agent.tool_registry import _make_mcp_routed_handler

    return _make_mcp_routed_handler(
        "swmm-runner", "swmm_run", args_mapper=_run_swmm_inp_args
    )


def __getattr__(name: str) -> Any:
    """Lazily construct ``_run_swmm_inp_tool`` on first access.

    The handler cannot be built eagerly at import time: doing so would
    pull ``_make_mcp_routed_handler`` from a still-loading
    ``tool_registry`` module. We defer the factory call to first
    attribute access via the PEP 562 module ``__getattr__`` hook, then
    promote the handler into the module dict so subsequent lookups
    skip the hook entirely (and so that
    ``from ... import _run_swmm_inp_tool`` returns a stable, identity-
    preserving object).
    """
    if name == "_run_swmm_inp_tool":
        import sys as _sys

        handler = _build_run_swmm_inp_tool()
        _sys.modules[__name__].__dict__[name] = handler
        return handler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["_run_swmm_inp_args", "_run_swmm_inp_tool"]
