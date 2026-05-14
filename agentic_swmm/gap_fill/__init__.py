"""Gap-fill runtime for L1 (missing file paths) and L3 (missing parameter values).

This package implements the detect/propose/resume state machine described in
PRD-GF-CORE. Submodules:

- :mod:`agentic_swmm.gap_fill.protocol` — canonical dataclass schemas
  (``GapSignal``, ``GapDecision``, ``GapBatch``, ``ProposerInfo``) that
  travel between tools, the runtime, the proposer, the UI, and the
  recorder.
- :mod:`agentic_swmm.gap_fill.preflight` — pre-flight L1 file-path
  scanner; pure function over a tool's declared inputs.
- :mod:`agentic_swmm.gap_fill.proposer` — layered proposer
  (registry → LLM-grounded → human fallthrough).
- :mod:`agentic_swmm.gap_fill.recorder` — atomic writer for
  ``<run_dir>/09_audit/gap_decisions.json`` plus a matching entry in
  ``experiment_provenance.json``.
- :mod:`agentic_swmm.gap_fill.ui` — batched TTY prompt for the
  combined L1+L3 form.

The package boundary is intentionally narrow: only ``protocol``,
``recorder``, ``preflight``, ``proposer``, and ``ui`` are exposed.
Higher-level downstream PRDs (GF-L5, GF-PROMOTE) extend this package
with their own modules.
"""

from agentic_swmm.gap_fill.protocol import (
    GapBatch,
    GapDecision,
    GapSignal,
    ProposerInfo,
    new_decision_id,
    new_gap_id,
)

__all__ = [
    "GapBatch",
    "GapDecision",
    "GapSignal",
    "ProposerInfo",
    "new_decision_id",
    "new_gap_id",
]
