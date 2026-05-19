"""Workflow-mode adapter registry (PRD-04).

Each runnable agent workflow ("prepared INP", "existing-run plot",
"audit only", etc.) is represented by one class in this package. The
registry collects them so ``OpenAIPlanner.run`` and
``_select_workflow_mode_tool`` can look up *spec* (required inputs /
recommended next tools / evidence boundary) and *behaviour* (the
``run`` method, when present) without keeping a hardcoded
``if mode == "x"`` table in either site.
"""

from agentic_swmm.agent.workflow_modes import base
from agentic_swmm.agent.workflow_modes.base import (
    WorkflowContext,
    WorkflowMode,
    all_modes,
    get_mode,
    get_mode_spec,
    register,
)

# Importing the adapter modules triggers their ``@register`` decorators
# so the registry is fully populated by import-time.
from agentic_swmm.agent.workflow_modes import (  # noqa: F401  (side-effect import)
    audit_only_or_comparison,
    calibration,
    existing_run_plot,
    full_modular_build,
    prepared_demo,
    prepared_inp,
    uncertainty,
)

__all__ = [
    "WorkflowContext",
    "WorkflowMode",
    "all_modes",
    "base",
    "get_mode",
    "get_mode_spec",
    "register",
]
