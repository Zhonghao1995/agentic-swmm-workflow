"""Postflight QA on the SWMM .rpt (PRD-06 Phase A.4).

Postflight reads the .rpt produced by SWMM, extracts the quantitative
QA metrics (continuity, eventually mass-balance and peak), classifies
each one against the project's ``reference_benchmarks.yaml`` thresholds,
and returns a structured :class:`QAReport`.

Why this lives separate from ``audit_run.py``
---------------------------------------------
``audit_run.py`` writes ``experiment_provenance.json`` — it already
extracts continuity. This module is *upstream* of audit: it is the
gate. If postflight FAILs, the audit pipeline should not advance.
Audit still records the failure for the trace, but a FAIL means
downstream consumers (plot, memory, calibration accept) refuse to
proceed.

Phase A scope is continuity only. Mass-balance / peak / runoff
classification arrive in Phase B with the comparison verb.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_swmm.memory.reference_benchmarks import (
    classify_metric,
    recall_reference_benchmark,
)


_RUNOFF_HEADER_RE = re.compile(r"Runoff\s+Quantity\s+Continuity", re.IGNORECASE)
_FLOW_HEADER_RE = re.compile(r"Flow\s+Routing\s+Continuity", re.IGNORECASE)
_CONTINUITY_RE = re.compile(
    r"Continuity\s+Error\s*\(%\)\s*\.*\s*(-?\d+\.\d+)"
)


@dataclass
class QAReport:
    """Structured outcome of :func:`postflight_qa`.

    Mirrors :class:`PreflightReport` shape so callers can render the
    pre- and post-flight checklists with the same UI primitives.
    """

    status: str = "PASS"
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    classifications: dict[str, str] = field(default_factory=dict)

    def _bump(self, severity: str) -> None:
        order = {"PASS": 0, "WARN": 1, "FAIL": 2, "UNKNOWN": 0}
        if order.get(severity, 0) > order.get(self.status, 0):
            self.status = severity

    def add_metric(self, name: str, value: float, classification: str) -> None:
        self.metrics[name] = value
        self.classifications[name] = classification
        if classification == "FAIL":
            self.failures.append(
                {"code": name, "detail": f"{name}={value} classified FAIL"}
            )
        elif classification == "WARN":
            self.warnings.append(
                {"code": name, "detail": f"{name}={value} classified WARN"}
            )
        self._bump(classification)


def parse_continuity_from_rpt(text: str) -> dict[str, float]:
    """Extract continuity errors from a .rpt body.

    Returns a dict with up to two keys: ``runoff_continuity_pct`` and
    ``flow_continuity_pct``. Each is the signed percentage SWMM
    reports — magnitude is what the classifier cares about.

    Missing sections are simply absent from the dict; we never raise
    on partial reports because SWMM truncates the .rpt when the
    simulation aborts early.
    """
    lines = text.splitlines()
    out: dict[str, float] = {}

    # We walk the file once, tracking which continuity block we are
    # inside. The first "Continuity Error" line after a block header
    # is the one we capture for that block.
    block: str | None = None
    for raw in lines:
        if _RUNOFF_HEADER_RE.search(raw):
            block = "runoff"
            continue
        if _FLOW_HEADER_RE.search(raw):
            block = "flow"
            continue
        m = _CONTINUITY_RE.search(raw)
        if m and block is not None:
            value = float(m.group(1))
            if block == "runoff":
                out["runoff_continuity_pct"] = value
            elif block == "flow":
                out["flow_continuity_pct"] = value
            block = None  # only capture the first error line per block

    return out


def _resolve_default_benchmarks_path() -> Path:
    """Return the repo-rooted default ``reference_benchmarks.yaml``."""
    # Two parents up: agentic_swmm/agent/swmm_runtime/ -> agentic_swmm/
    # ... up one more to repo root.
    return (
        Path(__file__).resolve().parents[3]
        / "memory"
        / "modeling-memory"
        / "reference_benchmarks.yaml"
    )


def _find_rpt(run_dir: Path) -> Path | None:
    """Locate a .rpt under ``run_dir``.

    Conventionally ``model.rpt`` is at the top of the run dir, but
    older runs nest it under ``03_run/`` or similar. We pick the
    first .rpt we find via shallow glob — postflight runs after the
    runner so there is exactly one in practice.
    """
    for candidate in sorted(run_dir.rglob("*.rpt")):
        return candidate
    return None


def postflight_qa(
    run_dir: Path, *, benchmarks_path: Path | None = None
) -> QAReport:
    """Parse ``run_dir``'s .rpt, classify continuity, return a :class:`QAReport`.

    ``benchmarks_path`` lets the caller swap in a custom thresholds
    YAML (tests, project-local overrides). Default is the repo's
    shipped ``memory/modeling-memory/reference_benchmarks.yaml``.
    """
    report = QAReport()
    run_dir = Path(run_dir)
    rpt = _find_rpt(run_dir)
    if rpt is None:
        report.failures.append(
            {"code": "rpt_missing", "detail": f"no .rpt under {run_dir}"}
        )
        report.status = "FAIL"
        return report

    try:
        text = rpt.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.failures.append(
            {"code": "rpt_unreadable", "detail": f"could not read {rpt}: {exc}"}
        )
        report.status = "FAIL"
        return report

    metrics = parse_continuity_from_rpt(text)
    benchmarks = benchmarks_path or _resolve_default_benchmarks_path()

    classification_map = {
        "runoff_continuity_pct": "continuity_thresholds_pct.runoff",
        "flow_continuity_pct": "continuity_thresholds_pct.flow",
    }

    for metric_name, value in metrics.items():
        dotted = classification_map.get(metric_name)
        thresholds = (
            recall_reference_benchmark(benchmarks, dotted, default={})
            if dotted
            else {}
        )
        classification = (
            classify_metric(value, thresholds) if thresholds else "UNKNOWN"
        )
        report.add_metric(metric_name, value, classification)

    return report
