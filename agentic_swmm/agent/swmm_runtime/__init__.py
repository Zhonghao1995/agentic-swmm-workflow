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

Package facade (ADR-0009): commands and tool handlers import submodules
off the package (``from agentic_swmm.agent.swmm_runtime import
run_layout``) instead of spelling file paths. Resolution is lazy
(PEP 562) so importing the package stays cheap and the historical
import order cannot form new cycles.
"""

from __future__ import annotations

import importlib

_SUBMODULES = frozenset(
    {
        "calibration_runner",
        "compare",
        "design_storm",
        "inp_parsing",
        "postflight",
        "preflight",
        "rpt_summary",
        "run_artifacts",
        "run_layout",
        "run_manifests",
        "uncertainty_plan",
        "version_compat",
    }
)


def __getattr__(name: str):
    if name in _SUBMODULES:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | _SUBMODULES)
