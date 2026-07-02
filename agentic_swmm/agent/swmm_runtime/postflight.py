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

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.agent.honesty import scan_rpt_for_errors

# API-stability re-export: the .rpt continuity parser moved to the
# canonical rpt module (rpt_summary owns the format knowledge; this
# module owns the QA bands and gating). Existing importers of
# ``postflight.parse_continuity_from_rpt`` keep working.
from agentic_swmm.agent.swmm_runtime.rpt_summary import (
    parse_continuity as parse_continuity_from_rpt,
)
from agentic_swmm.memory.benchmark_resolver import (
    default_project_overrides_path,
    resolve_threshold,
)
from agentic_swmm.memory.reference_benchmarks import classify_metric


# Library-conservative fallbacks. These match the SWMM User Manual's
# continuity-error magnitude bands and Phase A's shipped library so
# the runtime gate stays identical when neither the YAML nor a project
# overlay carries a value.
_FALLBACK_CONTINUITY_THRESHOLDS: dict[str, dict[str, float]] = {
    "runoff_continuity_pct": {"warn": 5.0, "fail": 10.0},
    "flow_continuity_pct": {"warn": 1.0, "fail": 5.0},
    "mass_balance_pct": {"warn": 2.0, "fail": 5.0},
}


@dataclass
class QAReport:
    """Structured outcome of :func:`postflight_qa`.

    Mirrors :class:`PreflightReport` shape so callers can render the
    pre- and post-flight checklists with the same UI primitives.

    Round 6 / PRD-07 Phase 4 extension: ``thresholds_source`` records
    which classification source actually decided each metric — one of
    ``"library"`` (the shipped reference benchmarks + project overlay)
    or ``"user_baseline"`` (the caller's own historical p95 / p99
    boundaries). Callers can render this in the chat note so the user
    sees *why* a given run was flagged.
    """

    status: str = "PASS"
    failures: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    classifications: dict[str, str] = field(default_factory=dict)
    thresholds_source: dict[str, str] = field(default_factory=dict)

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


def _classify_against_user_baseline(value: float, baseline: Any) -> str:
    """Three-tier classification against a :class:`UserBaseline`.

    Magnitude comparison: ``abs(value) > p99`` → FAIL,
    ``abs(value) > p95`` → WARN, else → PASS. Mirrors the library
    convention so the chat-note column reads uniformly regardless of
    which source classified the run.
    """
    magnitude = abs(float(value))
    p99 = float(getattr(baseline, "p99", 0.0))
    p95 = float(getattr(baseline, "p95", 0.0))
    if magnitude > p99:
        return "FAIL"
    if magnitude > p95:
        return "WARN"
    return "PASS"


def _write_postflight_memory_trace(
    run_dir: Path,
    *,
    thresholds_source: dict[str, str],
    user_baseline_percentile_used: dict[str, str],
) -> None:
    """Append one ``memory_trace.jsonl`` line describing the postflight gate.

    Best-effort: a failed write must not bubble up — the gate's
    primary obligation is to return a :class:`QAReport`, not to
    guarantee a log line. ``run_dir`` already exists at this point so
    the only failure mode is a read-only filesystem.
    """
    try:
        line = {
            "timestamp": (
                datetime.now(timezone.utc)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            ),
            "decision_point": "postflight_qa",
            "kind": "postflight_thresholds",
            "thresholds_source": dict(thresholds_source),
            "user_baseline_percentile_used": dict(
                user_baseline_percentile_used
            ),
            "schema_version": "1.0",
        }
        trace_path = run_dir / "memory_trace.jsonl"
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n"
            )
    except OSError:  # pragma: no cover - audit must never break dispatch
        return


def postflight_qa(
    run_dir: Path,
    *,
    benchmarks_path: Path | None = None,
    project_overrides_path: Path | None = None,
    parametric_store: Path | None = None,
    case_name: str | None = None,
    use_case: str | None = None,
) -> QAReport:
    """Parse ``run_dir``'s .rpt, classify continuity, return a :class:`QAReport`.

    ``benchmarks_path`` lets the caller swap in a custom default
    thresholds YAML (tests, project-local libraries). Default is the
    repo-shipped ``memory/modeling-memory/reference_benchmarks.yaml``.

    ``project_overrides_path`` (PRD-07 Phase 4) is an optional overlay
    YAML — same shape as the library — whose values win over the
    library leaf. When ``None``, the conventional location
    ``<memory_dir>/project_overrides.yaml`` is consulted; if that file
    is also missing the overlay is a no-op. Library nulls always fall
    through to the in-module conservative fallback so the runtime gate
    is never silently disabled.
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

    # PRD-08 A.1 (audit #1): scan for verbatim SWMM ERROR lines before
    # touching continuity. A solver error means the rpt cannot be
    # trusted — surface the first error line so downstream callers see
    # a structured FAIL instead of "continuity_parsed: false". The
    # continuity parse still runs because postflight has historically
    # been best-effort; bumping the report status to FAIL is enough to
    # gate the dispatch pipeline.
    error_lines = scan_rpt_for_errors(rpt)
    if error_lines:
        report.failures.append(
            {"code": "swmm_solver_error", "detail": error_lines[0]}
        )
        report._bump("FAIL")

    metrics = parse_continuity_from_rpt(text)
    benchmarks = benchmarks_path or _resolve_default_benchmarks_path()

    # Project overlay defaults to a sibling of the library so a
    # project-local override file is picked up without a CLI flag.
    if project_overrides_path is None:
        project_overrides_path = default_project_overrides_path(
            Path(benchmarks).parent
        )

    classification_map = {
        "runoff_continuity_pct": "continuity_thresholds_pct.runoff",
        "flow_continuity_pct": "continuity_thresholds_pct.flow",
    }
    # User-baseline metric paths — dotted JSON paths into the parametric
    # row. Mirrors classification_map's order so the lookup is one-shot
    # per metric.
    user_baseline_metric_path = {
        "runoff_continuity_pct": "qa_metrics.runoff_continuity_pct",
        "flow_continuity_pct": "qa_metrics.flow_continuity_pct",
    }

    user_baseline_enabled = (
        parametric_store is not None
        and case_name is not None
        and use_case is not None
    )
    # Lazy import so legacy callers do not pay the user_baseline cost.
    compute_user_baseline: Any = None
    if user_baseline_enabled:
        try:
            from agentic_swmm.memory.user_baseline import (
                compute_user_baseline as _cub,
            )

            compute_user_baseline = _cub
        except Exception:  # pragma: no cover - defensive
            compute_user_baseline = None

    percentile_used: dict[str, str] = {}

    for metric_name, value in metrics.items():
        dotted = classification_map.get(metric_name)
        fallback = _FALLBACK_CONTINUITY_THRESHOLDS.get(metric_name, {})

        baseline = None
        if user_baseline_enabled and compute_user_baseline is not None:
            metric_path = user_baseline_metric_path.get(metric_name)
            if metric_path is not None:
                try:
                    baseline = compute_user_baseline(
                        Path(parametric_store),
                        case_name=str(case_name),
                        use_case=str(use_case),
                        metric_path=metric_path,
                    )
                except Exception:  # pragma: no cover - defensive
                    baseline = None

        if baseline is not None:
            classification = _classify_against_user_baseline(value, baseline)
            magnitude = abs(float(value))
            if magnitude > float(getattr(baseline, "p99", 0.0)):
                percentile_used[metric_name] = "p99"
            elif magnitude > float(getattr(baseline, "p95", 0.0)):
                percentile_used[metric_name] = "p95"
            else:
                percentile_used[metric_name] = "<=p95"
            report.thresholds_source[metric_name] = "user_baseline"
            report.add_metric(metric_name, value, classification)
            continue

        thresholds = (
            resolve_threshold(
                dotted,
                reference_benchmarks_path=benchmarks,
                project_overrides_path=project_overrides_path,
                default=fallback,
            )
            if dotted
            else fallback
        )
        if not isinstance(thresholds, dict):
            thresholds = fallback
        # If the resolved dict still has null warn/fail (the Phase A
        # un-cited placeholder pattern), prefer the in-module fallback
        # so the runtime gate never silently degrades to UNKNOWN.
        if (
            thresholds.get("warn") is None
            and thresholds.get("fail") is None
            and fallback
        ):
            thresholds = fallback
        classification = (
            classify_metric(value, thresholds) if thresholds else "UNKNOWN"
        )
        report.thresholds_source[metric_name] = "library"
        report.add_metric(metric_name, value, classification)

    # Emit a memory_trace line documenting which source decided each
    # metric. We only write when user-baseline kwargs were supplied so
    # legacy callers see no new side-effects.
    if user_baseline_enabled and report.thresholds_source:
        _write_postflight_memory_trace(
            run_dir,
            thresholds_source=report.thresholds_source,
            user_baseline_percentile_used=percentile_used,
        )

    return report
