"""Project real QA artifacts into the dotted thresholds namespace.

The thresholds document declares dotted lookup keys
(``continuity.flow_routing``, ...); the QA writer emits a ``checks``
list with the numbers under ``detail``, which the evaluator's dict
walk can never reach. This module bridges the two so the gate can fire
on real runs (spec: fuzzy-hitl-gates).

Only namespaces with a verified producer are derived: continuity (from
the parsed-continuity check, compared on the absolute value with the
signed raw kept alongside), the calibration metrics (from the
``calibration_summary.json`` shape locked by
``tests/test_calibration_summary_schema.py``: KGE as the primary
objective, NSE and PBIAS as secondary metrics, PBIAS projected as its
absolute value) and the Sobol maximum first-order index.
``peak.deviation_percent`` and ``pour_point.suspect`` have no verified
producer today and stay absent, which the evaluator treats exactly as
before (missing key, no hit).
"""
from __future__ import annotations

from typing import Any


def project_qa(
    qa: dict[str, Any],
    *,
    sensitivity_indices: dict[str, Any] | None = None,
    calibration_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return only the derived namespaces; the caller merges them in
    without overriding keys the QA report already carries."""
    derived: dict[str, Any] = {}
    detail = _continuity_detail(qa)
    if detail:
        block: dict[str, float] = {}
        for key in ("flow_routing", "runoff_quantity"):
            value = detail.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                block[key] = abs(float(value))
                block[f"{key}_signed"] = float(value)
        if block:
            derived["continuity"] = block
    calibration = _calibration_metrics(calibration_summary)
    if calibration:
        derived["calibration"] = calibration
    s_i_max = _sobol_max_first_order(sensitivity_indices)
    if s_i_max is not None:
        derived["sensitivity"] = {"sobol": {"S_i_max": s_i_max}}
    return derived


def _continuity_detail(qa: dict[str, Any]) -> dict[str, Any] | None:
    checks = qa.get("checks")
    if not isinstance(checks, list):
        return None
    for check in checks:
        if isinstance(check, dict) and check.get("id") == "continuity_parsed":
            detail = check.get("detail")
            return detail if isinstance(detail, dict) else None
    return None


def _calibration_metrics(summary: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(summary, dict):
        return {}
    out: dict[str, float] = {}
    if summary.get("primary_objective") == "kge" and _is_number(
        summary.get("primary_value")
    ):
        out["kge"] = float(summary["primary_value"])
    secondary = summary.get("secondary_metrics")
    if isinstance(secondary, dict):
        if _is_number(secondary.get("nse")):
            out["nse"] = float(secondary["nse"])
        if _is_number(secondary.get("pbias_pct")):
            out["pbias_pct_abs"] = abs(float(secondary["pbias_pct"]))
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sobol_max_first_order(payload: dict[str, Any] | None) -> float | None:
    if not isinstance(payload, dict) or payload.get("method") != "sobol":
        return None
    indices = payload.get("indices")
    if not isinstance(indices, dict):
        return None
    values = [
        float(entry["S_i"])
        for entry in indices.values()
        if isinstance(entry, dict)
        and isinstance(entry.get("S_i"), (int, float))
        and not isinstance(entry.get("S_i"), bool)
    ]
    return max(values) if values else None
