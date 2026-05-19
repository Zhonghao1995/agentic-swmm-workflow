"""SWMM-aware runtime gates and verbs (PRD-06 Phase A + Phase B).

This sub-package brings SWMM domain knowledge to the agent runtime:

- :mod:`preflight` — INP sanity checks before invoking SWMM (Phase A.3)
- :mod:`postflight` — .rpt QA classification after the run (Phase A.4)
- :mod:`compare` — run-A-vs-run-B structured diff (Phase B.1)

Later phases add ``resource_planner``, ``storm_library``,
``units``, and ``version_compat``.
"""

from __future__ import annotations
