"""Run-vs-run comparison verb (PRD-06 Phase B.1).

A modeler typically runs the same INP twice with one parameter changed
and wants a structured diff — not a free-form essay. ``compare_runs``
takes two run directories, pulls the postflight QA metrics from each
via :func:`postflight_qa`, and returns a typed :class:`RunComparison`.

Scope is intentionally narrow for Phase B.1:

- Metric set: ``runoff_continuity_pct`` and ``flow_continuity_pct``.
  Mass-balance and per-node peak deltas arrive in later phases.
- Verdict is derived from the PASS/WARN/FAIL classifications postflight
  already produces — not from a re-computed magnitude rule. The
  classifier lives in :mod:`reference_benchmarks` so changing the
  thresholds only requires editing the YAML.
- Either run missing its .rpt yields ``verdict="incomparable"`` with a
  human-readable note. We never raise on a missing file because the
  CLI must stay usable mid-iteration.

Why this is upstream of audit
-----------------------------
``audit_run.py`` (in ``skills/swmm-experiment-audit``) writes a
``comparison.json`` when ``--compare-to`` is passed, but that artefact
is geared at the audit pipeline. ``compare_runs`` is the verb the
modeler — or the planner — calls *interactively* before deciding
which run to keep. It reuses postflight so the two paths never
disagree about a metric.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic_swmm.agent.swmm_runtime.postflight import QAReport, postflight_qa


# Default metric set for Phase B.1. Mass-balance and peak deltas arrive
# alongside the per-node comparison verb in a later phase — see PRD-06
# §4.7 for the full target metric list.
DEFAULT_METRICS: tuple[str, ...] = (
    "runoff_continuity_pct",
    "flow_continuity_pct",
)


# Order of classification severity. Lower is better. Used to decide which
# run is "better" when both have data.
_CLASSIFICATION_ORDER = {"PASS": 0, "WARN": 1, "FAIL": 2, "UNKNOWN": 3}


@dataclass
class MetricDiff:
    """Per-metric difference between two runs.

    ``delta_abs`` is ``value_b - value_a`` (signed). ``delta_pct`` is
    that delta normalised by ``|value_a|``, expressed as a percentage;
    it is ``None`` when ``value_a`` is zero or either value is missing
    so the caller does not need to special-case division-by-zero.
    """

    metric: str
    value_a: float | None = None
    value_b: float | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    classification_a: str = "UNKNOWN"
    classification_b: str = "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value_a": self.value_a,
            "value_b": self.value_b,
            "delta_abs": self.delta_abs,
            "delta_pct": self.delta_pct,
            "classification_a": self.classification_a,
            "classification_b": self.classification_b,
        }


@dataclass
class RunComparison:
    """Structured outcome of :func:`compare_runs`.

    ``verdict`` summarises the comparison:

    - ``"a_better"`` — run A's worst classification is strictly less
      severe than run B's, *or* both share the worst classification but
      run A's aggregate continuity magnitude is lower.
    - ``"b_better"`` — the mirror of ``a_better``.
    - ``"tie"`` — both runs share worst-class and aggregate magnitude
      within :data:`_TIE_TOL`.
    - ``"incomparable"`` — at least one run is missing the QA metrics
      we compare on. ``notes`` will explain why.
    """

    run_a_id: str
    run_b_id: str
    metric_diffs: dict[str, MetricDiff] = field(default_factory=dict)
    verdict: str = "tie"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_a_id": self.run_a_id,
            "run_b_id": self.run_b_id,
            "verdict": self.verdict,
            "notes": list(self.notes),
            "metric_diffs": {
                name: diff.to_dict() for name, diff in self.metric_diffs.items()
            },
        }


# Treat continuity-magnitude differences below this as a tie. Continuity
# is reported to three decimals by SWMM, so 1e-3 is the natural floor.
_TIE_TOL = 1e-3


def _resolve_run_id(run_dir: Path) -> str:
    """Read ``run_id`` from ``experiment_provenance.json`` if present.

    Falls back to the directory's basename. Tolerant of missing or
    malformed JSON — a comparison must not crash because a sibling
    audit artefact failed to materialise.
    """
    for relative in (
        Path("09_audit") / "experiment_provenance.json",
        Path("experiment_provenance.json"),
    ):
        candidate = run_dir / relative
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            run_id = payload.get("run_id")
            if isinstance(run_id, str) and run_id.strip():
                return run_id
    return run_dir.name


def _has_metrics(report: QAReport, metrics: tuple[str, ...]) -> bool:
    """Return ``True`` when ``report`` carries at least one of ``metrics``."""
    return any(name in report.metrics for name in metrics)


def _safe_delta_pct(value_a: float | None, value_b: float | None) -> float | None:
    """Percent change ``b vs a``, or ``None`` when undefined."""
    if value_a is None or value_b is None:
        return None
    if value_a == 0:
        return None
    return (value_b - value_a) / abs(value_a) * 100.0


def _build_metric_diffs(
    report_a: QAReport,
    report_b: QAReport,
    metrics: tuple[str, ...],
) -> dict[str, MetricDiff]:
    """Pair up per-metric numbers from each report into :class:`MetricDiff`s."""
    diffs: dict[str, MetricDiff] = {}
    for metric in metrics:
        value_a = report_a.metrics.get(metric)
        value_b = report_b.metrics.get(metric)
        delta_abs: float | None
        if value_a is not None and value_b is not None:
            delta_abs = float(value_b) - float(value_a)
        else:
            delta_abs = None
        diffs[metric] = MetricDiff(
            metric=metric,
            value_a=None if value_a is None else float(value_a),
            value_b=None if value_b is None else float(value_b),
            delta_abs=delta_abs,
            delta_pct=_safe_delta_pct(value_a, value_b),
            classification_a=report_a.classifications.get(metric, "UNKNOWN"),
            classification_b=report_b.classifications.get(metric, "UNKNOWN"),
        )
    return diffs


def _worst_classification(diffs: dict[str, MetricDiff], side: str) -> str:
    """Worst classification across all diffs for the ``"a"`` or ``"b"`` side."""
    worst = "PASS"
    for diff in diffs.values():
        cls = diff.classification_a if side == "a" else diff.classification_b
        if _CLASSIFICATION_ORDER.get(cls, 0) > _CLASSIFICATION_ORDER.get(worst, 0):
            worst = cls
    return worst


def _aggregate_magnitude(report: QAReport, metrics: tuple[str, ...]) -> float:
    """Sum of absolute metric values present in ``report``.

    Used as a tiebreaker when both runs share the same worst
    classification. Lower aggregate magnitude wins.
    """
    total = 0.0
    for name in metrics:
        value = report.metrics.get(name)
        if value is not None:
            total += abs(float(value))
    return total


def _decide_verdict(
    diffs: dict[str, MetricDiff],
    report_a: QAReport,
    report_b: QAReport,
    metrics: tuple[str, ...],
) -> tuple[str, list[str]]:
    """Return ``(verdict, notes)`` for the comparison."""
    notes: list[str] = []

    worst_a = _worst_classification(diffs, "a")
    worst_b = _worst_classification(diffs, "b")
    order_a = _CLASSIFICATION_ORDER.get(worst_a, 0)
    order_b = _CLASSIFICATION_ORDER.get(worst_b, 0)

    # Classification difference wins outright.
    if order_a < order_b:
        notes.append(
            f"run A worst-classification {worst_a} is better than run B's {worst_b}"
        )
        return "a_better", notes
    if order_b < order_a:
        notes.append(
            f"run B worst-classification {worst_b} is better than run A's {worst_a}"
        )
        return "b_better", notes

    # Classifications tied — fall back to aggregate continuity magnitude.
    mag_a = _aggregate_magnitude(report_a, metrics)
    mag_b = _aggregate_magnitude(report_b, metrics)
    if abs(mag_a - mag_b) <= _TIE_TOL:
        notes.append(
            f"both runs share worst-classification {worst_a} and "
            f"continuity magnitudes are within {_TIE_TOL}"
        )
        return "tie", notes
    if mag_a < mag_b:
        notes.append(
            f"runs share worst-classification {worst_a}; run A has "
            f"lower aggregate continuity magnitude ({mag_a:.3f} vs {mag_b:.3f})"
        )
        return "a_better", notes
    notes.append(
        f"runs share worst-classification {worst_a}; run B has "
        f"lower aggregate continuity magnitude ({mag_b:.3f} vs {mag_a:.3f})"
    )
    return "b_better", notes


def compare_runs(
    run_dir_a: Path,
    run_dir_b: Path,
    *,
    metrics: list[str] | None = None,
    benchmarks_path: Path | None = None,
) -> RunComparison:
    """Compare two SWMM run directories.

    ``metrics`` selects which QA metrics to diff; defaults to
    :data:`DEFAULT_METRICS`. ``benchmarks_path`` lets the caller pass a
    project-local thresholds YAML — by default postflight uses the
    repo-shipped library.

    Missing .rpt in either run yields ``verdict="incomparable"`` with a
    note pointing at the offending run.
    """
    run_dir_a = Path(run_dir_a)
    run_dir_b = Path(run_dir_b)

    metric_set: tuple[str, ...] = (
        tuple(metrics) if metrics is not None else DEFAULT_METRICS
    )

    run_a_id = _resolve_run_id(run_dir_a)
    run_b_id = _resolve_run_id(run_dir_b)

    report_a = postflight_qa(run_dir_a, benchmarks_path=benchmarks_path)
    report_b = postflight_qa(run_dir_b, benchmarks_path=benchmarks_path)

    diffs = _build_metric_diffs(report_a, report_b, metric_set)

    notes: list[str] = []
    if not _has_metrics(report_a, metric_set) and not _has_metrics(report_b, metric_set):
        notes.append(
            "neither run has the requested QA metrics — both .rpt files "
            "are missing or unparseable"
        )
        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metric_diffs=diffs,
            verdict="incomparable",
            notes=notes,
        )
    if not _has_metrics(report_a, metric_set):
        notes.append(f"run A ({run_a_id}) has no parseable QA metrics")
        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metric_diffs=diffs,
            verdict="incomparable",
            notes=notes,
        )
    if not _has_metrics(report_b, metric_set):
        notes.append(f"run B ({run_b_id}) has no parseable QA metrics")
        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metric_diffs=diffs,
            verdict="incomparable",
            notes=notes,
        )

    verdict, verdict_notes = _decide_verdict(diffs, report_a, report_b, metric_set)
    notes.extend(verdict_notes)

    # Tag legible PASS/FAIL observations so the human-readable table
    # has talking points without the caller re-deriving them.
    for diff in diffs.values():
        if diff.classification_a == "FAIL" and diff.classification_b == "PASS":
            notes.append(
                f"run A fails {diff.metric}; run B passes the same metric"
            )
        elif diff.classification_b == "FAIL" and diff.classification_a == "PASS":
            notes.append(
                f"run B fails {diff.metric}; run A passes the same metric"
            )

    return RunComparison(
        run_a_id=run_a_id,
        run_b_id=run_b_id,
        metric_diffs=diffs,
        verdict=verdict,
        notes=notes,
    )


def render_comparison_table(comparison: RunComparison) -> str:
    """Return a plain-text table for ``aiswmm compare`` default output.

    Kept tiny so a terminal-width-aware renderer can stay out of the
    runtime. Modelers eyeball this in a 100-col terminal; the column
    widths below fit that.
    """
    lines: list[str] = []
    lines.append(f"run A: {comparison.run_a_id}")
    lines.append(f"run B: {comparison.run_b_id}")
    lines.append(f"verdict: {comparison.verdict}")
    lines.append("")
    header = (
        f"{'metric':<28}{'value_a':>12}{'value_b':>12}"
        f"{'delta_abs':>12}{'class_a':>10}{'class_b':>10}"
    )
    lines.append(header)
    lines.append("-" * len(header))
    for name in sorted(comparison.metric_diffs):
        diff = comparison.metric_diffs[name]
        va = "n/a" if diff.value_a is None else f"{diff.value_a:.3f}"
        vb = "n/a" if diff.value_b is None else f"{diff.value_b:.3f}"
        da = "n/a" if diff.delta_abs is None else f"{diff.delta_abs:+.3f}"
        lines.append(
            f"{name:<28}{va:>12}{vb:>12}{da:>12}"
            f"{diff.classification_a:>10}{diff.classification_b:>10}"
        )
    if comparison.notes:
        lines.append("")
        lines.append("notes:")
        for note in comparison.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)
