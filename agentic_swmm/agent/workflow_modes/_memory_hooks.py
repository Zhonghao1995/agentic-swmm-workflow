"""Per-adapter memory-consult + gate hooks (Round 1 integration).

The runnable :class:`WorkflowMode` adapters all need the same five
operations at the top of their ``run`` method:

1. Resolve a ``case_name`` anchor.
2. Call ``MemoryIntegration.consult`` to populate
   ``ctx.memory_context``.
3. Emit a ``memory_consultation`` mirror event onto
   ``ctx.trace_path``.
4. For SWMM-running modes, run preflight and bail on FAIL.
5. After SWMM, run postflight and bail on FAIL.

This module factors those five operations into reusable functions so
each adapter's ``run`` body stays a small, readable wrapper around
the workflow logic and the memory hook does not have to be reimplemented
five times.

Failure mode
------------
Every helper is best-effort at the boundary. A failed memory consult
returns an empty :class:`MemoryContext`; a failed event write is
swallowed; a failed gate returns ``None`` (treated as "no gate ran").
The contract is that memory integration must never break dispatch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentic_swmm.agent.memory_context import MemoryContext


@dataclass(frozen=True)
class PreflightGateResult:
    """Outcome of the preflight gate from an adapter's perspective.

    ``ran`` is False when the gate was skipped (opt-out, missing
    integration). When the gate did run, ``ran`` is True and ``ok``
    indicates whether the INP passed; ``report`` carries the
    underlying :class:`PreflightReport` for the adapter to render
    when ``ok`` is False.
    """

    ran: bool
    ok: bool
    report: Any | None = None


@dataclass(frozen=True)
class PostflightGateResult:
    """Outcome of the postflight gate, same shape as Preflight."""

    ran: bool
    ok: bool
    report: Any | None = None


def consult_memory(ctx: Any) -> MemoryContext:
    """Run pre-run memory consultation against the context.

    Resolves the case_name anchor from ``ctx.case_name`` or the
    route's ``provided_values``, calls the integration's ``consult``,
    attaches the result to ``ctx.memory_context``, and emits the
    ``memory_consultation`` mirror event (when ``ctx.trace_path`` is
    set).

    Returns the :class:`MemoryContext` for callers that want to read
    it without going through ``ctx.memory_context``. An empty context
    is returned when no integration is wired or no case anchor is
    available.
    """
    integration = getattr(ctx, "memory_integration", None)
    if integration is None:
        ctx.memory_context = MemoryContext()
        return ctx.memory_context

    case_name = _resolve_case_name(ctx)
    context = integration.consult(case_name=case_name)
    ctx.memory_context = context

    _emit_consultation_event(ctx, case_name=case_name, context=context)
    return context


def run_preflight_gate(ctx: Any, inp_path: str) -> PreflightGateResult:
    """Run preflight against ``inp_path`` and return a structured result.

    The adapter inspects the result:

    * ``ran=False`` → the gate was opted out or the integration is
      missing; the adapter proceeds as if no gate existed.
    * ``ran=True, ok=False`` → FAIL; the adapter returns a
      ``PlannerRun(ok=False, ...)`` with the gate's narrative report.
    * ``ran=True, ok=True`` → PASS/WARN; the adapter proceeds.

    The narrative report is *not* surfaced through HITL because
    preflight is structural validation (missing inverts, zero-length
    conduits): the user has to fix the INP regardless.
    """
    integration = getattr(ctx, "memory_integration", None)
    if integration is None:
        return PreflightGateResult(ran=False, ok=True)

    report = integration.run_preflight(Path(inp_path))
    if report is None:
        return PreflightGateResult(ran=False, ok=True)
    status = getattr(report, "status", "FAIL")
    return PreflightGateResult(
        ran=True,
        ok=(status != "FAIL"),
        report=report,
    )


def run_postflight_gate(ctx: Any, run_dir: str) -> PostflightGateResult:
    """Run postflight against ``run_dir`` and return a structured result.

    Same conventions as :func:`run_preflight_gate`; the FAIL branch
    is meant to feed through ``hitl_surface.format_hitl_prompt``
    because postflight failures are *runtime* failures the user may
    want to override.
    """
    integration = getattr(ctx, "memory_integration", None)
    if integration is None:
        return PostflightGateResult(ran=False, ok=True)

    report = integration.run_postflight(Path(run_dir))
    if report is None:
        return PostflightGateResult(ran=False, ok=True)
    status = getattr(report, "status", "FAIL")
    return PostflightGateResult(
        ran=True,
        ok=(status != "FAIL"),
        report=report,
    )


def format_preflight_failure(report: Any, *, inp_path: str) -> str:
    """Render a preflight FAIL report into a chat-ready string."""
    failures = list(getattr(report, "failures", []) or [])
    warnings = list(getattr(report, "warnings", []) or [])
    lines: list[str] = [
        f"Preflight FAIL for {inp_path}.",
        "",
        "The following structural problems were detected before SWMM "
        "was invoked:",
    ]
    if failures:
        for row in failures:
            code = row.get("code", "?") if isinstance(row, dict) else "?"
            detail = row.get("detail", "") if isinstance(row, dict) else ""
            lines.append(f"  - [{code}] {detail}")
    if warnings:
        lines.append("")
        lines.append("Warnings (non-blocking):")
        for row in warnings:
            code = row.get("code", "?") if isinstance(row, dict) else "?"
            detail = row.get("detail", "") if isinstance(row, dict) else ""
            lines.append(f"  - [{code}] {detail}")
    lines.append("")
    lines.append(
        "Fix the FAIL entries and re-run, or set "
        "AISWMM_DISABLE_SWMM_GATES=1 to bypass."
    )
    return "\n".join(lines)


def format_postflight_failure(
    report: Any,
    *,
    run_dir: str,
    memory_context: Any | None,
    decision_point: str = "postflight_qa",
) -> str:
    """Render a postflight FAIL report through the HITL surface.

    Postflight failures (continuity out of bounds, runaway routing
    error) are runtime failures the user may legitimately want to
    review, override, or fix in calibration. Routing them through
    ``format_hitl_prompt`` gives the user the same structured prompt
    as any other HITL escalation: what the agent was about to do,
    why human input is required, and what memory had to say.
    """
    from agentic_swmm.agent.hitl_surface import format_hitl_prompt

    failures = list(getattr(report, "failures", []) or [])
    warnings = list(getattr(report, "warnings", []) or [])
    metrics = dict(getattr(report, "metrics", {}) or {})
    classifications = dict(getattr(report, "classifications", {}) or {})

    fail_lines: list[str] = [f"Postflight QA FAIL for {run_dir}."]
    if failures:
        fail_lines.append("Failures:")
        for row in failures:
            code = row.get("code", "?") if isinstance(row, dict) else "?"
            detail = row.get("detail", "") if isinstance(row, dict) else ""
            fail_lines.append(f"  - [{code}] {detail}")
    if warnings:
        fail_lines.append("Warnings:")
        for row in warnings:
            code = row.get("code", "?") if isinstance(row, dict) else "?"
            detail = row.get("detail", "") if isinstance(row, dict) else ""
            fail_lines.append(f"  - [{code}] {detail}")
    if metrics:
        fail_lines.append(
            "Metrics: "
            + ", ".join(
                f"{k}={metrics[k]} ({classifications.get(k, 'UNKNOWN')})"
                for k in sorted(metrics)
            )
        )

    return format_hitl_prompt(
        "\n".join(fail_lines),
        memory_context if isinstance(memory_context, MemoryContext) else MemoryContext(),
        decision_point=decision_point,
        proposed_action=f"continue downstream processing of {run_dir}",
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_case_name(ctx: Any) -> str | None:
    """Pick the best-effort case anchor for a workflow context.

    Order of precedence:
        1. ``ctx.case_name`` set explicitly by the planner.
        2. ``ctx.route.provided_values.case_id``.
        3. Slug derived from ``ctx.session_dir.name``
           (``HHMMSS_<case>_<run|chat>``).
        4. ``None``.
    """
    direct = getattr(ctx, "case_name", None)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    route = getattr(ctx, "route", None) or {}
    provided = route.get("provided_values") or {}
    if isinstance(provided, dict):
        case_id = provided.get("case_id")
        if isinstance(case_id, str) and case_id.strip():
            return case_id.strip()
    session_dir = getattr(ctx, "session_dir", None)
    if session_dir is not None:
        match = _SESSION_DIR_CASE_RE.match(Path(session_dir).name)
        if match:
            return match.group("case")
    return None


_SESSION_DIR_CASE_RE = re.compile(
    r"^\d+_(?P<case>.+?)_(?:run|chat)(?:_\d+)?$"
)


def _emit_consultation_event(
    ctx: Any, *, case_name: str | None, context: MemoryContext
) -> None:
    """Best-effort write of the ``memory_consultation`` mirror event."""
    trace_path = getattr(ctx, "trace_path", None)
    if trace_path is None:
        return
    try:
        from agentic_swmm.agent.reporting import write_memory_consultation

        write_memory_consultation(
            Path(trace_path),
            kind="workflow_defaults",
            case_meta={"case_name": case_name} if case_name else {},
            evidence_count=context.parametric_hit_count,
            consensus_fields=[],
            ambiguous_fields=[],
            queried_at_utc=(
                context.provenance.get("gathered_at_utc")
                if isinstance(context.provenance, dict)
                else None
            ),
        )
    except Exception:  # pragma: no cover - defensive
        return


__all__ = [
    "PostflightGateResult",
    "PreflightGateResult",
    "consult_memory",
    "format_postflight_failure",
    "format_preflight_failure",
    "run_postflight_gate",
    "run_preflight_gate",
]
