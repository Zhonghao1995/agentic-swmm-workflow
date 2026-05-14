"""Classify a user prompt into one of four execution paths.

PRD_runtime "Module: Continuation classifier":

- ``classify(prompt, workflow_state) -> ExecutionPath``
- ``ExecutionPath = Enum{NEW_SWMM_RUN, NEW_CHAT, PLOT_CONTINUATION, UNCLEAR}``
- ``PLOT_CONTINUATION`` when ``active_run_dir`` is set AND the prompt
  matches the plot-continuation heuristic: it contains a node id or
  one of the known variable keywords (``inflow``, ``depth``, ``flow``,
  ``peak``, ``plot``, 等).

The classifier is pure — no I/O. It is consumed by ``runtime_loop``
to bypass ``select_workflow_mode`` when ``PLOT_CONTINUATION`` is
returned with an active run.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any


class ExecutionPath(str, Enum):
    NEW_SWMM_RUN = "new_swmm_run"
    NEW_CHAT = "new_chat"
    PLOT_CONTINUATION = "plot_continuation"
    UNCLEAR = "unclear"


# Heuristic keyword sets — keep small and explicit so they are easy to
# audit. The PRD lists ``inflow, depth, flow, peak`` as the variable
# vocabulary; the wider plot vocabulary mirrors
# ``runtime_loop._looks_like_run_continuation``.
_PLOT_KEYWORDS = (
    "plot",
    "figure",
    "graph",
    "rainfall",
    "outfall",
    "inflow",
    "depth",
    "flow",
    "peak",
    "total_inflow",
    "depth_above_invert",
    "volume_stored_ponded",
    "flow_lost_flooding",
    "hydraulic_head",
    "作图",
    "画图",
    "图",
    "水深",
    "节点",
    "根据你刚才",
    "刚才的运行",
)

# Vocabulary that signals "build / run a new SWMM model" — overrides
# plot continuation even if an active run is present.
_BUILD_KEYWORDS = (
    "build",
    "create a new",
    "new model",
    "new run",
    "another run",
    "重新跑",
    "新建",
    "重新建",
)

# A SWMM run prompt without an active run: explicit .inp path or run
# verb.
_NEW_RUN_KEYWORDS = (
    ".inp",
    "run swmm",
    "run examples",
    "run the model",
    "tecnopolo",
    "todcreek",
)

_NODE_ID_PATTERN = re.compile(r"\b[JO]\d+\b", flags=re.IGNORECASE)


def classify(prompt: str, workflow_state: dict[str, Any] | None) -> ExecutionPath:
    """Return the planner's intended execution path for ``prompt``.

    ``workflow_state`` is the dict written by ``state.write_session_state``
    or its equivalent shape; the only key consulted here is
    ``active_run_dir``.
    """
    if not isinstance(prompt, str):
        return ExecutionPath.UNCLEAR
    text = prompt.strip()
    if not text:
        return ExecutionPath.UNCLEAR
    lowered = text.lower()

    state = workflow_state if isinstance(workflow_state, dict) else {}
    active_run_dir = state.get("active_run_dir")

    has_build_intent = any(token in lowered for token in _BUILD_KEYWORDS)
    has_node_id = bool(_NODE_ID_PATTERN.search(text))
    has_plot_vocab = any(token in lowered for token in _PLOT_KEYWORDS)
    has_new_run_marker = any(token in lowered for token in _NEW_RUN_KEYWORDS)

    if has_build_intent:
        # Explicit build/new-run wins over continuation even with an
        # active run.
        return ExecutionPath.NEW_SWMM_RUN

    if active_run_dir:
        if has_plot_vocab or has_node_id:
            return ExecutionPath.PLOT_CONTINUATION
        return ExecutionPath.UNCLEAR

    # No active run.
    if has_new_run_marker:
        return ExecutionPath.NEW_SWMM_RUN
    return ExecutionPath.NEW_CHAT
