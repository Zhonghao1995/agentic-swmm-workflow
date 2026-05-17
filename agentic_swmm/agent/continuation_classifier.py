"""Classify a user prompt into one of four execution paths.

PRD_runtime "Module: Continuation classifier":

- ``classify(prompt, workflow_state) -> ExecutionPath``
- ``ExecutionPath = Enum{NEW_SWMM_RUN, NEW_CHAT, PLOT_CONTINUATION, UNCLEAR}``
- ``PLOT_CONTINUATION`` when ``active_run_dir`` is set AND the prompt
  matches the plot-continuation heuristic: it contains a node id or
  one of the known variable keywords (``inflow``, ``depth``, ``flow``,
  ``peak``, ``plot``, 等).

The classifier is pure — no I/O. PRD #121 moved the keyword tables
into ``agentic_swmm.agent.intent_classifier`` (the single source of
truth for keyword-driven intent extraction); this module is now a thin
adapter that maps the resulting ``IntentSignals`` to ``ExecutionPath``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from agentic_swmm.agent.intent_classifier import classify_intent


class ExecutionPath(str, Enum):
    NEW_SWMM_RUN = "new_swmm_run"
    NEW_CHAT = "new_chat"
    PLOT_CONTINUATION = "plot_continuation"
    UNCLEAR = "unclear"


def classify(prompt: str, workflow_state: dict[str, Any] | None) -> ExecutionPath:
    """Return the planner's intended execution path for ``prompt``.

    ``workflow_state`` is the dict written by ``state.write_session_state``
    or its equivalent shape; the only key consulted here is
    ``active_run_dir``.
    """
    if not isinstance(prompt, str):
        return ExecutionPath.UNCLEAR
    if not prompt.strip():
        return ExecutionPath.UNCLEAR

    signals = classify_intent(prompt, workflow_state=workflow_state)

    if signals.has_build_intent:
        # Explicit build/new-run wins over continuation even with an
        # active run.
        return ExecutionPath.NEW_SWMM_RUN

    state = workflow_state if isinstance(workflow_state, dict) else {}
    active_run_dir = state.get("active_run_dir")

    if active_run_dir:
        if signals.has_plot_continuation_vocab or signals.has_node_id:
            return ExecutionPath.PLOT_CONTINUATION
        return ExecutionPath.UNCLEAR

    # No active run.
    if signals.has_new_run_marker:
        return ExecutionPath.NEW_SWMM_RUN
    return ExecutionPath.NEW_CHAT
