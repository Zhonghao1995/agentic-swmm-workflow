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

These three layers together turn the audit/provenance pipeline from a
post-hoc record into a runtime governance gate: the agent can pause at
hard QA thresholds, the modeller can make a decision via CLI, and the
provenance file separates "agent decided" from "human decided".
"""

from agentic_swmm.hitl.threshold_evaluator import (
    ThresholdHit,
    evaluate,
    load_thresholds_from_md,
)

__all__ = [
    "ThresholdHit",
    "evaluate",
    "load_thresholds_from_md",
]
