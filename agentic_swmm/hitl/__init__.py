"""HITL (Human-In-The-Loop) governance subsystem (PRD-Z).

This package adds three coupled mechanisms on top of the audit layer:

1. ``threshold_evaluator`` — pure evaluator that turns a QA report and a
   thresholds table into a list of ``ThresholdHit`` records. Thresholds
   are loaded from ``docs/hitl-thresholds.md`` (a hand-edited config
   document whose ``rationale`` fields the human modeller fills in).
2. ``decision_recorder`` — atomic appender for ``human_decisions``
   entries inside a run's ``experiment_provenance.json`` (schema 1.2).
3. The ``request_expert_review`` agent tool (registered in
   ``agentic_swmm.agent.tool_registry``) — a pause-and-prompt seam that
   blocks the agent until the human answers Y/N on stdin.
4. ``qa_projection`` / ``banding`` — the graded-gate half (spec
   fuzzy-hitl-gates): project real QA artifact shapes into the dotted
   namespace the thresholds doc declares, and grade banded entries
   low / medium / high instead of a crisp cutoff.

These three layers together turn the audit/provenance pipeline from a
post-hoc record into a runtime governance gate: the agent can pause at
hard QA thresholds, the modeller can make a decision via CLI, and the
provenance file separates "agent decided" from "human decided".
"""

from agentic_swmm.hitl.decision_recorder import (
    HumanDecision,
    append_decision,
    make_decision,
    new_decision_id,
    now_utc_iso,
    read_decisions,
)
from agentic_swmm.hitl.qa_projection import project_qa
from agentic_swmm.hitl.threshold_evaluator import (
    ThresholdHit,
    evaluate,
    load_thresholds_from_md,
)

# Deliberately NOT re-exported: ``request_expert_review`` (the function)
# collides with ``request_expert_review`` (its submodule). Python's
# submodule binding shadows any package-level re-export of the same name
# once the submodule has been imported, so a facade route would return
# the module or the function depending on import order. Callers import
# it from the submodule; the import-surface guard documents this as the
# single sanctioned hitl submodule import (issue #359).
__all__ = [
    "HumanDecision",
    "ThresholdHit",
    "append_decision",
    "evaluate",
    "load_thresholds_from_md",
    "make_decision",
    "new_decision_id",
    "now_utc_iso",
    "project_qa",
    "read_decisions",
]
