"""Reference benchmarks loader (PRD-06 Phase A.2).

A SWMM modeler asks "is NSE 0.65 acceptable for an urban stormwater
model" — generic LLM answers vary by region, by year, by use case.
``memory/modeling-memory/reference_benchmarks.yaml`` is the curated,
hand-editable, version-controlled source of truth for the *project*.

This module is the typed reader. It is deliberately small:

- :func:`load_reference_benchmarks` — load the YAML, tolerant of
  missing / malformed files
- :func:`recall_reference_benchmark` — dotted-key lookup with default
- :func:`classify_metric` — given an observed value and a
  ``{warn, fail}`` threshold dict, return ``"PASS" | "WARN" | "FAIL"``

The classifier is the bridge to :mod:`agentic_swmm.agent.swmm_runtime.postflight`:
postflight reads a metric, looks up the relevant threshold dict via
:func:`recall_reference_benchmark`, then calls :func:`classify_metric`.
That keeps thresholds out of the postflight module — they belong to
the project's curated library, not to the runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_reference_benchmarks(path: Path) -> dict[str, Any]:
    """Load ``path`` as a YAML dict; return ``{}`` on any failure.

    Missing files and malformed YAML both yield ``{}`` so a fresh
    project (no curated benchmarks yet) does not need to special-case
    "library not initialised" — every consumer of this function
    already handles "key not present" via :func:`recall_reference_benchmark`
    defaults.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def recall_reference_benchmark(
    path: Path, dotted_key: str, default: Any = None
) -> Any:
    """Look up ``dotted_key`` (e.g. ``"continuity_thresholds_pct.runoff.warn"``).

    Returns ``default`` if any part of the path is missing. Callers
    typically pass a numeric default that's safely conservative
    (e.g. ``5.0`` for a continuity warn threshold) so the calling code
    can stay branchless.
    """
    data = load_reference_benchmarks(path)
    cursor: Any = data
    for part in dotted_key.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def classify_metric(value: float, thresholds: dict[str, Any]) -> str:
    """Return ``"PASS" | "WARN" | "FAIL" | "UNKNOWN"`` for ``value``.

    ``thresholds`` is expected to look like
    ``{"warn": 5.0, "fail": 10.0}``. The classifier is magnitude-based
    so a SWMM continuity error of ``-7%`` (signed) classifies the same
    as ``+7%`` — a modeler reading the .rpt sees the magnitude in the
    sentence "Continuity Error (%) ....." regardless of sign.

    Returns ``"UNKNOWN"`` when the thresholds dict is missing the
    expected keys, so downstream callers can surface that the YAML
    library is incomplete rather than silently mark everything PASS.
    """
    warn = thresholds.get("warn")
    fail = thresholds.get("fail")
    if warn is None or fail is None:
        return "UNKNOWN"
    magnitude = abs(float(value))
    if magnitude >= float(fail):
        return "FAIL"
    if magnitude >= float(warn):
        return "WARN"
    return "PASS"
