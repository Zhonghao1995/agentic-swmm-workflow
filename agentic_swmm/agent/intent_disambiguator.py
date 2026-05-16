"""LLM-based disambiguator for plot-conflict compound intents (issue #111).

Background
==========

The planner's auto-route fast-path mis-classifies compound goals like
``"run Tod Creek demo and plot the figure"`` as ``existing_run_plot`` —
the keyword fallback priority places ``wants_plot AND has_run_dir``
ahead of ``wants_demo``, so the agent plots an unrelated prior run
instead of running the demo the user asked for.

Hybrid design
=============

This module implements **LLM disambiguation only on detected
conflict**. The narrow trigger is::

    wants_plot AND (wants_demo OR wants_run OR
                    wants_calibration OR wants_uncertainty)

When the trigger fires we call the provider with a single forced
fake-tool (``classify_workflow_mode(mode: enum)``) and return the
LLM-picked mode string. On any failure path — no trigger, invalid
mode, timeout, arbitrary exception — we return ``None`` so the caller
falls through to the keyword classifier (which, after this PRD, has
its priority bug fixed too).

The 7-value mode enum is kept in sync with
``tool_registry._VALID_MODE_ENUM`` via direct import.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Protocol

from agentic_swmm.agent.tool_registry import _VALID_MODE_ENUM
from agentic_swmm.providers.base import ProviderToolResponse


# Public so tests / planner trace events can read the same constant
# rather than reinventing the set.
PLOT_CONFLICT_SIGNALS = ("wants_demo", "wants_run", "wants_calibration", "wants_uncertainty")


_FAKE_TOOL_NAME = "classify_workflow_mode"

# The fake tool spec the LLM is *forced* to emit. The handler is never
# invoked — the planner reads ``tool_call.arguments.mode`` directly.
_FAKE_TOOL_SCHEMA = {
    "type": "function",
    "name": _FAKE_TOOL_NAME,
    "description": (
        "Classify the user's goal into exactly one workflow mode. "
        "Required."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": sorted(_VALID_MODE_ENUM),
                "description": "The workflow mode that best matches the user's primary intent.",
            }
        },
        "required": ["mode"],
        "additionalProperties": False,
    },
}


_SYSTEM_PROMPT = """You classify a SWMM user's natural-language goal into one of 7 workflow modes.

Modes:
- calibration: tune model parameters against observed flow (NSE/KGE).
- uncertainty: fuzzy / sensitivity / scenario sweep over an existing INP.
- prepared_inp_cli: execute a prepared .inp file via swmm5 CLI.
- full_modular_build: build .inp from scratch using network + climate + params.
- existing_run_plot: plot results from an already-completed run directory.
- audit_only_or_comparison: audit / compare existing run artifacts (no new run).
- prepared_demo: run the project's prepared acceptance demo (Tod Creek etc.).

Rule for compound intent: pick the main *action* verb. When a user
says "run X and plot the figure", the primary action is to run X;
plotting is a follow-up artifact of that run, so the correct mode is
the one matching the run verb (e.g. prepared_demo, prepared_inp_cli),
NOT existing_run_plot.

Call the classify_workflow_mode tool exactly once with the chosen mode."""


class _Provider(Protocol):
    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse: ...


def _trigger_fires(signals: dict[str, bool]) -> bool:
    """Return True when the goal has a plot verb *and* another action verb."""
    if not signals.get("wants_plot"):
        return False
    return any(signals.get(name) for name in PLOT_CONFLICT_SIGNALS)


def disambiguate(
    goal: str,
    signals: dict[str, bool],
    provider: _Provider,
    timeout_sec: float = 5.0,
    on_response: Callable[[ProviderToolResponse, tuple[str, list[dict[str, Any]]], int], None] | None = None,
) -> str | None:
    """Return the LLM-picked workflow mode, or ``None`` on any failure.

    ``goal`` is the raw user goal string. ``signals`` carries the
    keyword-derived ``wants_*`` flags computed by the caller (the
    planner) — we do NOT recompute them here so the trigger condition
    and the keyword fallback share a single source of truth.

    ``provider`` must expose ``respond_with_tools(...)``; see the
    ``OpenAIProvider`` implementation.

    ``on_response`` is an optional observer hook the caller uses to
    funnel the provider call through the LLM-trace observer
    (``record_llm_call``). It receives ``(response, prompt_tuple,
    duration_ms)`` where ``prompt_tuple`` is the
    ``(system_prompt, input_items)`` pair the disambiguator sent.
    The hook is called only on a successful provider response — not
    on timeout or exception — so the audit reflects what the LLM
    actually returned. The hook is best-effort: any exception it
    raises is swallowed so audit failures cannot break workflow
    dispatch.

    Returns ``None`` when:
      * the trigger does not fire (no conflict to resolve);
      * the LLM does not emit the fake tool call;
      * the LLM emits a mode that is not in the 7-value enum;
      * the provider raises any exception (timeout, network, …);
      * the LLM call exceeds ``timeout_sec`` seconds (default 5s).
    """

    if not _trigger_fires(signals):
        return None

    input_items = [{"role": "user", "content": goal}]
    start = time.monotonic()
    response = _call_with_timeout(provider, input_items, timeout_sec)
    duration_ms = int((time.monotonic() - start) * 1000)
    if response is None:
        return None

    if on_response is not None:
        try:
            on_response(response, (_SYSTEM_PROMPT, input_items), duration_ms)
        except Exception:  # pragma: no cover - audit must never break dispatch
            pass

    for call in response.tool_calls:
        if call.name != _FAKE_TOOL_NAME:
            continue
        mode = call.arguments.get("mode") if isinstance(call.arguments, dict) else None
        if isinstance(mode, str) and mode in _VALID_MODE_ENUM:
            return mode
    return None


def _call_with_timeout(
    provider: _Provider,
    input_items: list[dict[str, Any]],
    timeout_sec: float,
) -> ProviderToolResponse | None:
    """Invoke ``provider.respond_with_tools`` with a wall-clock timeout.

    We thread-wrap the call because the public ``OpenAIProvider`` API
    does not expose a per-call timeout argument (its constructor takes
    a connection timeout, but a slow API response would still bubble
    up to the planner). Any exception inside the worker thread — or
    the thread failing to join within ``timeout_sec`` — is swallowed
    and surfaces as ``None`` to the caller.
    """

    result: dict[str, Any] = {}

    def _worker() -> None:
        try:
            result["response"] = provider.respond_with_tools(
                system_prompt=_SYSTEM_PROMPT,
                input_items=input_items,
                tools=[_FAKE_TOOL_SCHEMA],
            )
        except Exception:  # pragma: no cover - exception path covered via tests
            result["error"] = True

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=timeout_sec)
    if thread.is_alive():
        # Timed out — the daemon thread is abandoned. The planner falls
        # back to the keyword classifier; the abandoned worker either
        # completes silently or is reaped at interpreter exit.
        return None
    response = result.get("response")
    if isinstance(response, ProviderToolResponse):
        return response
    return None


__all__ = ["disambiguate", "PLOT_CONFLICT_SIGNALS"]
