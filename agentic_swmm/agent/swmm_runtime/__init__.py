"""SWMM-aware runtime gates and verbs (PRD-06 Phase A + Phase B).

This sub-package brings SWMM domain knowledge to the agent runtime:

- :mod:`preflight` — INP sanity checks before invoking SWMM (Phase A.3)
- :mod:`postflight` — .rpt QA classification after the run (Phase A.4)
- :mod:`compare` — run-A-vs-run-B structured diff (Phase B.1; Round 3
  adds per-node/per-subcatch diffs and SWMM solver-version refusal)
- :mod:`version_compat` — guards cross-version comparisons so a
  modeler does not mistake solver-behaviour deltas for parameter
  deltas (Round 3)
- :mod:`uncertainty_plan` — sensitivity/Monte-Carlo planner
- :mod:`design_storm` — Chicago / Huff / SCS hyetograph generators
"""

from __future__ import annotations
