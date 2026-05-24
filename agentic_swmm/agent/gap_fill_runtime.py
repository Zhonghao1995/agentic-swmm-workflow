"""Gap-fill state machine (PRD-GF-CORE) — extracted from ``runtime_loop``.

CONCURRENCY-OWNER: PRD-GF-CORE

This module owns the runtime-side state machine for L1 (missing file
paths) and L3 (missing parameter values) gaps. The split out of
``runtime_loop.py`` (Issue #205) keeps the gap-fill subsystem
independent of the bootstrap phases that share the same file. The
contract is unchanged:

    invoke_tool_with_gap_fill(spec, call, session_dir, base_invoke)
        └── pre-flight L1 scan over ``spec.required_file_args``
        └── call ``base_invoke(call, session_dir)`` to run the tool
        └── if ``result.gap_signal``: collect, propose, ui-review,
            record, re-invoke the tool with merged args
        └── attach ``gap_filled: [...]`` to the final success result
            so the LLM sees what was filled in

The wrapper is invoked from
``tool_registry.AgentToolRegistry.execute`` (also marked
CONCURRENCY-OWNER: PRD-GF-CORE). The split keeps the orchestration
logic out of ``tool_registry.py`` while the registry stays the
actual dispatch seam.

Bug class to watch: if the wrapper raises (proposer registry-only
miss, UI rejection), the runtime returns a fail-soft result dict
rather than propagating — the planner's contract is "execute()
returns a dict, never raises". The error is folded into ``summary``
and ``ok=False``.
"""

from __future__ import annotations

import os
import sys as _sys


__all__ = [
    "invoke_tool_with_gap_fill",
    "is_tty",
    "gap_fill_disabled",
]


def gap_fill_disabled() -> bool:
    """Return True iff the operator has set ``AISWMM_GAP_DISABLE=1``.

    PRD-GF-CORE ships a per-tool ``supports_gap_fill`` flag *and* a
    global kill-switch so a regression can be rolled back without a
    code revert.
    """
    value = os.environ.get("AISWMM_GAP_DISABLE")
    if value is None:
        return False
    return value.strip().lower() not in {"", "0", "false", "no"}


def is_tty() -> bool:
    """Return True iff both stdin and stdout look like a real TTY.

    The UI uses this to decide whether to render the batched form or
    fall through to the env-var matrix. Tests can drive the matrix
    by setting the matching env vars; production CI hits the non-TTY
    branch automatically.
    """
    try:
        return bool(_sys.stdin.isatty() and _sys.stdout.isatty())
    except Exception:
        return False


def invoke_tool_with_gap_fill(spec, call, session_dir, base_invoke):
    """Run ``spec.handler`` through the L1+L3 gap-fill state machine.

    The wrapper is no-op (just calls ``base_invoke``) when:

    - ``spec.supports_gap_fill`` is False, or
    - ``AISWMM_GAP_DISABLE=1`` is set, or
    - the spec has no declared ``required_file_args`` AND the first
      result has no ``gap_signal`` (i.e. no gaps to fill).

    Otherwise the wrapper runs the full detect → propose → review →
    record → retry loop and returns the resumed tool's result with a
    ``gap_filled: [...]`` field appended.

    Parameters:

    - ``spec``: the :class:`agentic_swmm.agent.tool_registry.ToolSpec`.
    - ``call``: the :class:`agentic_swmm.agent.types.ToolCall`.
    - ``session_dir``: the per-session run directory (used for
      audit writes).
    - ``base_invoke``: a callable ``(call, session_dir) -> dict``
      that actually runs the handler. Decoupled so the registry's
      pre-call permission/profile checks stay in one place.
    """
    if gap_fill_disabled() or not getattr(spec, "supports_gap_fill", False):
        return base_invoke(call, session_dir)

    # Imports kept local — gap-fill modules are only needed when the
    # wrapper actually fires, and a missing pyyaml on a minimal venv
    # should not break tool_registry import-time.
    from agentic_swmm.gap_fill.preflight import scan_required_files
    from agentic_swmm.gap_fill.proposer import GapFillRegistryOnlyMiss, propose_batch
    from agentic_swmm.gap_fill.protocol import GapSignal
    from agentic_swmm.gap_fill.recorder import record_gap_decisions
    from agentic_swmm.gap_fill.ui import (
        GapFillNonInteractive,
        GapFillRejected,
        review_batch,
    )

    merged_args: dict[str, object] = dict(call.args)
    all_resolved = []
    # Two retries max: one for pre-flight L1, one for in-band L3, plus
    # a guard so a buggy tool that keeps emitting the same gap can't
    # loop forever. The PRD allows L1+L3 in one batch but we keep the
    # iteration count tight.
    for attempt in range(3):
        # Pre-flight L1 scan happens BEFORE the tool runs so we never
        # invoke a tool with a path that won't open.
        l1_signals = []
        required = getattr(spec, "required_file_args", ()) or ()
        if required:
            l1_signals = scan_required_files(
                tool_name=spec.name,
                required_file_args=required,
                args=merged_args,
            )

        if l1_signals:
            resolved = _resolve_gap_batch(
                l1_signals,
                tool_name=spec.name,
                session_dir=session_dir,
                propose_batch=propose_batch,
                review_batch=review_batch,
                record_gap_decisions=record_gap_decisions,
            )
            if resolved is None:
                # User rejected / non-interactive failure — fall back
                # to a fail-soft result the planner can surface.
                return {
                    "tool": call.name,
                    "args": call.args,
                    "ok": False,
                    "summary": "gap-fill aborted (L1 paths could not be resolved)",
                }
            for dec in resolved:
                merged_args[dec.field] = dec.final_value
            all_resolved.extend(resolved)
            # Loop back to re-run the pre-flight scan (in case the
            # new path itself doesn't exist).
            continue

        # Now run the tool.
        from agentic_swmm.agent.types import ToolCall as _ToolCall

        invocation = _ToolCall(name=call.name, args=dict(merged_args))
        try:
            result = base_invoke(invocation, session_dir)
        except (GapFillRejected, GapFillNonInteractive, GapFillRegistryOnlyMiss) as exc:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": f"gap-fill aborted: {exc}",
                "return_code": 1,
            }

        gap_payload = result.get("gap_signal") if isinstance(result, dict) else None
        if not gap_payload:
            # Tool succeeded (or failed for non-gap reasons). Attach
            # the cumulative gap_filled list and return.
            if all_resolved and isinstance(result, dict) and result.get("ok"):
                result = dict(result)
                result["gap_filled"] = [
                    {
                        "field": d.field,
                        "final_value": d.final_value,
                        "source": d.proposer.source,
                        "decision_id": d.decision_id,
                    }
                    for d in all_resolved
                ]
            return result

        try:
            signal = GapSignal.from_dict(gap_payload)
        except (ValueError, TypeError):
            # Tool emitted a malformed gap_signal; surface as failure
            # rather than guessing.
            return result

        resolved = _resolve_gap_batch(
            [signal],
            tool_name=spec.name,
            session_dir=session_dir,
            propose_batch=propose_batch,
            review_batch=review_batch,
            record_gap_decisions=record_gap_decisions,
        )
        if resolved is None:
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": "gap-fill aborted (L3 parameters could not be resolved)",
            }
        for dec in resolved:
            merged_args[dec.field] = dec.final_value
        all_resolved.extend(resolved)

    # Out of retries — the tool keeps emitting gap signals. Return a
    # loud failure so the planner doesn't loop forever upstream.
    return {
        "tool": call.name,
        "args": call.args,
        "ok": False,
        "summary": (
            "gap-fill retry budget exhausted after 3 attempts; "
            "the tool kept emitting gap_signal"
        ),
    }


def _resolve_gap_batch(
    signals,
    *,
    tool_name,
    session_dir,
    propose_batch,
    review_batch,
    record_gap_decisions,
):
    """Run propose → ui → record over a batch of signals.

    Returns the list of :class:`GapDecision` with ``final_value`` set,
    or ``None`` if the user rejected the batch or the non-interactive
    path failed. Errors are folded to ``None`` so the caller can
    return a fail-soft result; the exception messages are written to
    the planner trace via stderr.
    """
    try:
        proposals = propose_batch(
            signals=signals,
            run_dir=session_dir,
            llm_proposal_fn=None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _sys.stderr.write(f"GAP_FILL_PROPOSE_ERROR: {exc}\n")
        _sys.stderr.flush()
        return None
    try:
        # Indirect via the runtime_loop re-export so existing tests
        # that ``mock.patch("agentic_swmm.agent.runtime_loop._is_tty")``
        # keep working. The runtime_loop module re-exports ``is_tty``
        # as ``_is_tty`` from this module, and the patch replaces that
        # re-bound symbol; we therefore look it up at call time.
        from agentic_swmm.agent import runtime_loop as _runtime_loop

        is_tty_now = _runtime_loop._is_tty()
        resolved = review_batch(
            proposals,
            tool_name=tool_name,
            is_tty=is_tty_now,
        )
    except Exception as exc:
        _sys.stderr.write(f"GAP_FILL_UI_ERROR: {exc}\n")
        _sys.stderr.flush()
        return None
    try:
        recorded = record_gap_decisions(session_dir, resolved)
    except Exception as exc:  # pragma: no cover - defensive
        _sys.stderr.write(f"GAP_FILL_RECORD_ERROR: {exc}\n")
        _sys.stderr.flush()
        return None
    return recorded
