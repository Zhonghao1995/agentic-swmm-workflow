"""SWMM-aware runtime gates and verbs (PRD-06 Phase A).

This sub-package brings SWMM domain knowledge to the agent runtime:

- :mod:`preflight` — INP sanity checks before invoking SWMM
- :mod:`postflight` — .rpt QA classification after the run

Phase B+ adds ``comparison``, ``resource_planner``, ``storm_library``,
``units``, and ``version_compat`` (out of scope here).
"""

from __future__ import annotations
