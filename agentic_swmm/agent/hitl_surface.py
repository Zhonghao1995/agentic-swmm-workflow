"""HITL prompt surface (PRD-06 Phase D.2).

When :class:`MemoryHITLRequired` is raised the runtime currently
surfaces the bare exception ``args[0]`` as the user-facing message.
That message is fine for the audit log but unhelpful in a chat
transcript — the user sees only the escalation sentence and has no
view into *what the agent knew from memory* when it escalated.

This module formats a multi-line plain-text prompt that combines:
    * what the agent was about to do (``proposed_action``);
    * why the human is required (``escalation_message``);
    * what the agent knows from memory (``memory_context``); and
    * a clear closing question.

The result is one string. We deliberately do not return structured
JSON: the prompt has to render correctly in a chat REPL, an Obsidian
note, and a CI log without a templating layer. Plain text fits all
three.

Failure mode
------------
:func:`format_hitl_prompt` never raises. An empty
:class:`MemoryContext`, a missing ``escalation_message``, and a
``proposed_action`` of ``None`` are all valid inputs — the function
still returns a non-empty string. The runtime catches
:class:`MemoryHITLRequired` exactly once; if the formatter were
allowed to raise, an escalation that the policy explicitly flagged
would silently collapse into a generic error message.
"""

from __future__ import annotations

import statistics
from typing import Any

from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord


# Closing question. Hardcoded — making it configurable would invite
# spell-checking creep in every locale; the prompt is short by design.
_CLOSING_QUESTION = "Please confirm or override."


def _summarise_metric(values: list[float]) -> str | None:
    """Return ``"min=… max=… mean=…"`` for a non-empty list, else ``None``.

    Three decimals matches the SWMM continuity report's precision.
    """
    if not values:
        return None
    lo = min(values)
    hi = max(values)
    mean = statistics.fmean(values)
    return f"min={lo:.3f}, max={hi:.3f}, mean={mean:.3f}"


def _gather_metric(hits: list[ParametricRecord], key: str) -> list[float]:
    """Return numeric values for ``key`` across all hits, dropping blanks.

    Lives next to :func:`_summarise_metric` so the metric extraction
    stays trivial — qa_metrics is a free-form dict, so we just coerce
    each candidate to ``float`` and skip non-numerics.
    """
    out: list[float] = []
    for h in hits:
        v = h.qa_metrics.get(key)
        if v is None:
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _format_known_metrics(hits: list[ParametricRecord]) -> list[str]:
    """Return a list of "metric: stats" lines summarising prior QA.

    Two metrics today (``runoff_continuity_pct`` and
    ``flow_continuity_pct``) — both are reported in every SWMM run, so
    a non-empty hits list almost always yields at least one line. The
    function returns an empty list when no metric has any numeric
    coverage, which the caller handles gracefully.
    """
    out: list[str] = []
    for key, label in (
        ("runoff_continuity_pct", "runoff continuity %"),
        ("flow_continuity_pct", "flow continuity %"),
    ):
        values = _gather_metric(hits, key)
        fragment = _summarise_metric(values)
        if fragment:
            out.append(f"  {label}: {fragment} (n={len(values)})")
    return out


def _format_recency_line(hits: list[ParametricRecord]) -> str | None:
    """Return ``"most recent run: <run_id> at <recorded_utc>"`` or ``None``.

    The recency line tells the user how stale the memory's evidence
    is. When the hits are all undated we omit the line rather than
    invent a sort order.
    """
    dated = [h for h in hits if (h.recorded_utc or "").strip()]
    if not dated:
        return None
    latest = max(dated, key=lambda h: h.recorded_utc or "")
    return f"  most recent run: {latest.run_id} at {latest.recorded_utc}"


def _safe_str(value: Any) -> str:
    """Return ``str(value)`` but never let an exotic ``__str__`` raise.

    The exception class wraps any object; a hostile ``__str__`` would
    otherwise propagate through the formatter and defeat the
    "never raises" contract.
    """
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return "(unprintable)"


def format_hitl_prompt(
    escalation_message: str,
    memory_context: MemoryContext,
    *,
    decision_point: str = "unknown",
    proposed_action: str | None = None,
) -> str:
    """Format a multi-line HITL prompt for the user.

    Arguments:
        escalation_message: The ``args[0]`` of the raised
            :class:`MemoryHITLRequired`. Trimmed and rendered verbatim
            in the "why" section. Empty strings are accepted; the
            section renders a placeholder.
        memory_context: The :class:`MemoryContext` the policy
            consulted. Empty contexts are common (first run on a
            fresh case) and produce a "memory has nothing to anchor
            this decision" stanza.
        decision_point: A short label for the audit trail
            (``"planner_intent_disambiguation"`` etc.). Surfaced in
            the header so the human knows where in the pipeline the
            escalation fired.
        proposed_action: One-line description of what the agent was
            about to do. ``None`` produces a "(no proposed action
            recorded)" placeholder so the prompt structure stays
            consistent.

    Returns:
        A plain-text prompt with sections separated by blank lines.
        The string ends with the closing question; callers do not
        need to append their own.
    """
    try:
        message = (escalation_message or "").strip()
        if not message:
            message = "(no escalation message provided)"

        action = (proposed_action or "").strip() or "(no proposed action recorded)"

        header = (
            f"Memory escalation at {decision_point or 'unknown'}: human "
            "input required."
        )

        lines: list[str] = [
            header,
            "",
            "What the agent was about to do:",
            f"  {action}",
            "",
            "Why human input is required:",
            f"  {message}",
            "",
            "What the agent knows from memory:",
        ]

        hits = list(getattr(memory_context, "parametric_hits", []) or [])
        summary = _safe_str(getattr(memory_context, "summary", "") or "").strip()

        if summary:
            lines.append(f"  summary: {summary}")
        lines.append(f"  parametric hits: {len(hits)}")

        recency = _format_recency_line(hits)
        if recency:
            lines.append(recency)

        metric_lines = _format_known_metrics(hits)
        lines.extend(metric_lines)

        if not summary and not hits:
            lines.append("  (no prior runs or thresholds were recorded)")

        lines.extend(["", _CLOSING_QUESTION])
        return "\n".join(lines)
    except Exception:  # pragma: no cover - defensive
        # The formatter must never raise. Falling through to a
        # minimal prompt preserves the escalation contract: the user
        # still sees that human input is required, even if every
        # other section blew up.
        return (
            "Memory escalation: human input required.\n\n"
            f"{escalation_message or '(no escalation message provided)'}\n\n"
            f"{_CLOSING_QUESTION}"
        )


__all__ = ["format_hitl_prompt"]
