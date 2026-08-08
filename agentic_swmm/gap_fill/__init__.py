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
- :mod:`agentic_swmm.gap_fill.ui_per_gap` — per-gap prompt variant the
  gap-fill tool handler drives.
- :mod:`agentic_swmm.gap_fill.llm_enumerator` — GF-L5's LLM-grounded
  gap enumerator.

The package boundary is intentionally narrow: only the submodules
listed above are exposed (issue #359 grew the list to match the two
GF-L5 modules the tool handler already consumed). The allowed external
import surface is pinned by ``tests/test_package_import_surfaces.py``;
growing it is a conscious edit to that pin in the same PR.
"""

from agentic_swmm.gap_fill.protocol import (
    GapBatch,
    GapCandidate,
    GapDecision,
    GapSignal,
    ProposerInfo,
    new_decision_id,
    new_gap_id,
)

__all__ = [
    "GapBatch",
    "GapCandidate",
    "GapDecision",
    "GapSignal",
    "ProposerInfo",
    "new_decision_id",
    "new_gap_id",
]
