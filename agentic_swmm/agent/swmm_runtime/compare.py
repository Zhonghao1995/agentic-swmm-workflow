"""Run-vs-run comparison verb (PRD-06 Phase B.1 + Round 3 deepening).

A modeler typically runs the same INP twice with one parameter changed
and wants a structured diff — not a free-form essay. ``compare_runs``
takes two run directories, pulls the postflight QA metrics from each
via :func:`postflight_qa`, and returns a typed :class:`RunComparison`.

Round 3 extends the diff surface beyond aggregate continuity. The
verdict is still continuity-driven (continuity is the gate); the new
per-node and per-subcatch tables are informational so the modeler can
see *which* elements moved most between runs without re-running an
audit.

- Aggregate metrics: ``runoff_continuity_pct`` and ``flow_continuity_pct``.
- Per-node: max-lateral/total inflow and time-of-max from the .rpt
  ``Node Inflow Summary`` block.
- Per-subcatch: total runoff (mm and 10^6 L) from the .rpt
  ``Subcatchment Runoff Summary`` block.
- Top-movers: the 5 nodes / subcatches with the largest absolute
  percent change between runs.
- SWMM solver-version refusal: if both runs report a parseable
  ``swmm_version`` and the versions are not byte-compatible we bail
  early with ``verdict="incomparable"`` unless the caller passes
  ``override_version=True``.

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

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.swmm_runtime.postflight import QAReport, postflight_qa
from agentic_swmm.agent.swmm_runtime.rpt_summary import section_data_lines
from agentic_swmm.agent.swmm_runtime.version_compat import (
    SwmmVersionCompatVerdict,
    check_swmm_versions_for_compare,
)
from agentic_swmm.memory.benchmark_resolver import resolve_threshold


# Tiebreaker tolerance for aggregate-continuity comparisons. Centralised
# via :func:`resolve_threshold` so a project can loosen the floor for
# coarse-precision .rpt outputs without touching this module.
_TIE_TOL_DEFAULT = 1e-3
_TIE_TOL_DOTTED_KEY = "compare_runs.tie_tol_continuity_magnitude"


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
class NodePeak:
    """Per-node peak-flow numbers parsed from the ``Node Inflow Summary``.

    The .rpt records both lateral (locally generated) inflow and total
    (lateral + routed) inflow as separate columns. We keep both because
    a downstream LID intervention can change lateral without moving
    total (or vice versa).

    ``time_of_max`` is the SWMM-formatted string (``"days hr:min"``);
    we expose it as-is so a downstream renderer can format. Downstream
    callers that need a numeric minute offset compute it themselves.
    """

    node: str
    max_lateral_inflow: float | None = None
    max_total_inflow: float | None = None
    time_of_max: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "max_lateral_inflow": self.max_lateral_inflow,
            "max_total_inflow": self.max_total_inflow,
            "time_of_max": self.time_of_max,
        }


@dataclass
class SubcatchRunoff:
    """Per-subcatch runoff numbers parsed from ``Subcatchment Runoff Summary``.

    Both depth (mm) and volume (10^6 L) are present in the .rpt; we
    keep both so comparison consumers can normalise either way.
    """

    subcatch: str
    total_runoff_mm: float | None = None
    total_runoff_volume_10_6L: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subcatch": self.subcatch,
            "total_runoff_mm": self.total_runoff_mm,
            "total_runoff_volume_10_6L": self.total_runoff_volume_10_6L,
        }


@dataclass
class NodePeakDiff:
    """Diff of one node's peak-inflow between two runs.

    ``delta_pct`` is the percent change in ``max_total_inflow``
    normalised by ``|value_a|``; ``None`` when ``value_a`` is zero or
    either side is missing. ``time_shift_min`` is the difference in
    time-of-max expressed in minutes (run B minus run A); ``None`` when
    either side is missing or unparseable.
    """

    node: str
    peak_a: NodePeak | None = None
    peak_b: NodePeak | None = None
    delta_max_total_inflow: float | None = None
    delta_pct: float | None = None
    time_shift_min: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "peak_a": self.peak_a.to_dict() if self.peak_a else None,
            "peak_b": self.peak_b.to_dict() if self.peak_b else None,
            "delta_max_total_inflow": self.delta_max_total_inflow,
            "delta_pct": self.delta_pct,
            "time_shift_min": self.time_shift_min,
        }


@dataclass
class SubcatchRunoffDiff:
    """Diff of one subcatch's runoff depth between two runs.

    ``delta_total_runoff_mm`` is ``mm_b - mm_a``; ``delta_pct`` is that
    delta normalised by ``|mm_a|`` as a percentage. Either is ``None``
    when the corresponding value is missing or zero.
    """

    subcatch: str
    runoff_a: SubcatchRunoff | None = None
    runoff_b: SubcatchRunoff | None = None
    delta_total_runoff_mm: float | None = None
    delta_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "subcatch": self.subcatch,
            "runoff_a": self.runoff_a.to_dict() if self.runoff_a else None,
            "runoff_b": self.runoff_b.to_dict() if self.runoff_b else None,
            "delta_total_runoff_mm": self.delta_total_runoff_mm,
            "delta_pct": self.delta_pct,
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

    Round-3 fields (``node_peak_diffs``, ``subcatch_runoff_diffs``,
    ``top_movers_*``) are additive and default to empty: a comparison
    against a .rpt that lacks the per-element sections still returns a
    valid :class:`RunComparison`; the per-element views are simply
    empty rather than raising. The verdict logic is unchanged — it
    stays continuity-driven so the gate behaviour is stable as the
    surface grows.
    """

    run_a_id: str
    run_b_id: str
    metric_diffs: dict[str, MetricDiff] = field(default_factory=dict)
    verdict: str = "tie"
    notes: list[str] = field(default_factory=list)
    node_peak_diffs: dict[str, NodePeakDiff] = field(default_factory=dict)
    subcatch_runoff_diffs: dict[str, SubcatchRunoffDiff] = field(default_factory=dict)
    top_movers_nodes: list[tuple[str, float]] = field(default_factory=list)
    top_movers_subcatches: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_a_id": self.run_a_id,
            "run_b_id": self.run_b_id,
            "verdict": self.verdict,
            "notes": list(self.notes),
            "metric_diffs": {
                name: diff.to_dict() for name, diff in self.metric_diffs.items()
            },
            "node_peak_diffs": {
                node: diff.to_dict() for node, diff in self.node_peak_diffs.items()
            },
            "subcatch_runoff_diffs": {
                sub: diff.to_dict() for sub, diff in self.subcatch_runoff_diffs.items()
            },
            "top_movers_nodes": [
                [name, value] for name, value in self.top_movers_nodes
            ],
            "top_movers_subcatches": [
                [name, value] for name, value in self.top_movers_subcatches
            ],
        }


# Treat continuity-magnitude differences below this as a tie. Continuity
# is reported to three decimals by SWMM, so 1e-3 is the natural floor.
# This is now resolved per-invocation via :func:`resolve_threshold`; the
# module-level constant is the conservative default when no overlay or
# library leaf is configured.
_TIE_TOL = _TIE_TOL_DEFAULT


# ---------------------------------------------------------------------------
# .rpt per-element parsers (Round 3).
#
# These are line-walkers, not regex grammars: SWMM's .rpt is whitespace-
# separated and section-delimited, and a grammar would over-fit subtle
# vertical layout differences across SWMM minor versions. The walkers
# below find a section's data block by header + dashed-line markers,
# then split each data row on whitespace. Unparseable rows are skipped
# rather than treated as errors — partial data is better than no data.
# ---------------------------------------------------------------------------


def _safe_float(token: str) -> float | None:
    """Parse a token as float; return ``None`` on failure.

    SWMM sometimes prints ``N/A`` or ``****`` in numeric cells when the
    underlying simulation produced no data. Treat those as "missing"
    so a downstream consumer sees a clean ``None`` instead of a
    sentinel float.
    """
    try:
        return float(token)
    except (TypeError, ValueError):
        return None


def parse_node_peaks_from_rpt(text: str) -> dict[str, NodePeak]:
    """Extract per-node peak inflow + time-of-max from a SWMM .rpt.

    Returns an empty dict when the ``Node Inflow Summary`` block is
    absent or unparseable. Each node maps to a :class:`NodePeak`.

    The .rpt row format is whitespace-separated::

        J1   JUNCTION   1.184   1.184   2  13:54   107   107   0.001

    Columns: ``name type max_lateral max_total day hr:min vol_lat
    vol_tot balance_err``. We pick out ``max_lateral`` (col 2 after
    name+type), ``max_total`` (col 3), and join ``day hr:min`` for the
    time-of-max string. The rest of the row is ignored; a future
    extension can capture flow-balance error too.
    """
    out: dict[str, NodePeak] = {}
    for raw in section_data_lines(text, "Node Inflow Summary"):
        tokens = raw.split()
        if len(tokens) < 6:
            # Minimum: name type max_lat max_total day hr:min
            continue
        name = tokens[0]
        # tokens[1] is the type label (e.g. JUNCTION, OUTFALL, STORAGE,
        # DIVIDER). Move past it.
        try:
            max_lat = _safe_float(tokens[2])
            max_tot = _safe_float(tokens[3])
            # day + hr:min are two tokens.
            time_of_max = f"{tokens[4]} {tokens[5]}"
        except IndexError:
            continue
        out[name] = NodePeak(
            node=name,
            max_lateral_inflow=max_lat,
            max_total_inflow=max_tot,
            time_of_max=time_of_max,
        )
    return out


def parse_subcatch_runoff_from_rpt(text: str) -> dict[str, SubcatchRunoff]:
    """Extract per-subcatch runoff depth + volume from a SWMM .rpt.

    Returns an empty dict when ``Subcatchment Runoff Summary`` is
    absent. The .rpt row carries 10 numeric columns; the ones we keep
    are ``Total Runoff (mm)`` and ``Total Runoff (10^6 ltr)``.

    Row format (after the subcatch name)::

        precip runon evap infil imperv_runoff perv_runoff
        total_runoff_mm total_runoff_10_6L peak_cms runoff_coeff

    so the mm value is index 7 (after the name) and the 10^6 L value
    is index 8.
    """
    out: dict[str, SubcatchRunoff] = {}
    for raw in section_data_lines(text, "Subcatchment Runoff Summary"):
        tokens = raw.split()
        # Minimum: name + 10 numeric columns
        if len(tokens) < 11:
            continue
        name = tokens[0]
        total_mm = _safe_float(tokens[7])
        total_vol = _safe_float(tokens[8])
        out[name] = SubcatchRunoff(
            subcatch=name,
            total_runoff_mm=total_mm,
            total_runoff_volume_10_6L=total_vol,
        )
    return out


def _parse_time_of_max_minutes(token: str | None) -> float | None:
    """Convert a SWMM ``"days hr:min"`` string into total minutes.

    Returns ``None`` on unparseable input so a downstream caller never
    confuses "unknown" with "zero".
    """
    if not token:
        return None
    parts = token.split()
    if len(parts) != 2:
        return None
    try:
        days = int(parts[0])
    except ValueError:
        return None
    hr_min = parts[1].split(":")
    if len(hr_min) != 2:
        return None
    try:
        hr = int(hr_min[0])
        mn = int(hr_min[1])
    except ValueError:
        return None
    return float(days * 24 * 60 + hr * 60 + mn)


def _build_node_peak_diffs(
    peaks_a: dict[str, NodePeak], peaks_b: dict[str, NodePeak]
) -> dict[str, NodePeakDiff]:
    """Pair up :class:`NodePeak`s by node name into :class:`NodePeakDiff`s.

    A node present in only one run gets a diff entry with the missing
    side as ``None`` and the deltas as ``None``; this lets the caller
    surface "appeared / disappeared" without re-deriving from the raw
    dicts.
    """
    all_nodes = sorted(set(peaks_a) | set(peaks_b))
    out: dict[str, NodePeakDiff] = {}
    for name in all_nodes:
        pa = peaks_a.get(name)
        pb = peaks_b.get(name)
        delta_abs: float | None = None
        delta_pct: float | None = None
        time_shift: float | None = None
        if pa and pb:
            va = pa.max_total_inflow
            vb = pb.max_total_inflow
            if va is not None and vb is not None:
                delta_abs = float(vb) - float(va)
                delta_pct = _safe_delta_pct(va, vb)
            ta = _parse_time_of_max_minutes(pa.time_of_max)
            tb = _parse_time_of_max_minutes(pb.time_of_max)
            if ta is not None and tb is not None:
                time_shift = tb - ta
        out[name] = NodePeakDiff(
            node=name,
            peak_a=pa,
            peak_b=pb,
            delta_max_total_inflow=delta_abs,
            delta_pct=delta_pct,
            time_shift_min=time_shift,
        )
    return out


def _build_subcatch_runoff_diffs(
    sub_a: dict[str, SubcatchRunoff], sub_b: dict[str, SubcatchRunoff]
) -> dict[str, SubcatchRunoffDiff]:
    """Pair up :class:`SubcatchRunoff`s by subcatch name."""
    all_sub = sorted(set(sub_a) | set(sub_b))
    out: dict[str, SubcatchRunoffDiff] = {}
    for name in all_sub:
        ra = sub_a.get(name)
        rb = sub_b.get(name)
        delta_abs: float | None = None
        delta_pct: float | None = None
        if ra and rb:
            va = ra.total_runoff_mm
            vb = rb.total_runoff_mm
            if va is not None and vb is not None:
                delta_abs = float(vb) - float(va)
                delta_pct = _safe_delta_pct(va, vb)
        out[name] = SubcatchRunoffDiff(
            subcatch=name,
            runoff_a=ra,
            runoff_b=rb,
            delta_total_runoff_mm=delta_abs,
            delta_pct=delta_pct,
        )
    return out


def _rank_top_movers(
    diffs: dict[str, NodePeakDiff] | dict[str, SubcatchRunoffDiff],
    *,
    limit: int = 5,
) -> list[tuple[str, float]]:
    """Return the top ``limit`` entries by absolute ``delta_pct``.

    Entries with ``delta_pct is None`` are excluded — a "moved most"
    ranking should only contain elements where the delta is defined.
    """
    ranked: list[tuple[str, float]] = []
    for name, diff in diffs.items():
        pct = getattr(diff, "delta_pct", None)
        if pct is None:
            continue
        ranked.append((name, float(pct)))
    ranked.sort(key=lambda pair: (-abs(pair[1]), pair[0]))
    return ranked[: max(0, int(limit))]


def _experiment_provenance_candidates(run_dir: Path) -> list[Path]:
    """Candidate ``experiment_provenance.json`` paths, canonical-first.

    ADR-0004: ``run_layout.AUDIT`` (``09_audit``) is the canonical audit
    stage; ``run_layout.find_stage`` resolves it and falls back to the
    legacy ``06_audit`` generation (``run_layout.LEGACY_ALIASES``) when
    the canonical dir is absent. The bare run-dir-root path is the
    oldest (pre-audit-stage) flat layout and stays as the last resort.
    """
    candidates: list[Path] = []
    audit_dir = run_layout.find_stage(run_dir, run_layout.AUDIT)
    if audit_dir is not None:
        candidates.append(audit_dir / "experiment_provenance.json")
    candidates.append(run_dir / "experiment_provenance.json")
    return candidates


def _read_swmm_version_for_run(
    run_dir: Path, run_id: str, parametric_store: Path | None = None
) -> str | None:
    """Return the SWMM solver version recorded for ``run_dir``.

    Lookup order:

    1. ``run_dir/09_audit/experiment_provenance.json`` ``swmm_version`` field
       (or its legacy ``06_audit`` generation).
    2. ``run_dir/experiment_provenance.json`` ``swmm_version`` field.
    3. ``parametric_store`` (parametric_memory.jsonl): row with matching
       ``run_id``, then ``swmm_version`` field.

    Returns ``None`` if no source carries the field. Defensive: every
    I/O failure (missing file, malformed JSON, unreadable directory)
    is swallowed so the comparison verb stays usable even when the
    audit artefact is half-populated.
    """
    for candidate in _experiment_provenance_candidates(run_dir):
        if not candidate.is_file():
            continue
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            v = payload.get("swmm_version")
            if isinstance(v, str) and v.strip():
                return v.strip()
    if parametric_store is not None:
        try:
            from agentic_swmm.memory.parametric_memory import recall_parametric

            rows = recall_parametric(parametric_store, {"run_id": run_id})
            for row in rows:
                v = row.get("swmm_version")
                if isinstance(v, str) and v.strip():
                    return v.strip()
        except Exception:  # pragma: no cover - defensive
            return None
    return None


def _resolve_run_id(run_dir: Path) -> str:
    """Read ``run_id`` from ``experiment_provenance.json`` if present.

    Falls back to the directory's basename. Tolerant of missing or
    malformed JSON — a comparison must not crash because a sibling
    audit artefact failed to materialise.
    """
    for candidate in _experiment_provenance_candidates(run_dir):
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
    *,
    tie_tol: float = _TIE_TOL_DEFAULT,
) -> tuple[str, list[str]]:
    """Return ``(verdict, notes)`` for the comparison.

    ``tie_tol`` lets the caller plug in a project-overlay-resolved
    tolerance — see :func:`compare_runs`. The default preserves the
    historical 1e-3 magnitude floor.
    """
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
    if abs(mag_a - mag_b) <= tie_tol:
        notes.append(
            f"both runs share worst-classification {worst_a} and "
            f"continuity magnitudes are within {tie_tol}"
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


def _find_rpt_text(run_dir: Path) -> str | None:
    """Return the contents of the run's .rpt, or ``None`` when absent.

    Mirrors :func:`postflight._find_rpt` but inlined here so the
    per-element parsers do not pay a second QA pass. We accept a
    decode error tolerantly because the .rpt is plain ASCII and a
    failure usually means a stray byte at end-of-file rather than a
    structural problem.
    """
    for candidate in sorted(run_dir.rglob("*.rpt")):
        try:
            return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return None


def compare_runs(
    run_dir_a: Path,
    run_dir_b: Path,
    *,
    metrics: list[str] | None = None,
    benchmarks_path: Path | None = None,
    project_overrides_path: Path | None = None,
    override_version: bool = False,
    parametric_store: Path | None = None,
) -> RunComparison:
    """Compare two SWMM run directories.

    ``metrics`` selects which QA metrics to diff; defaults to
    :data:`DEFAULT_METRICS`. ``benchmarks_path`` lets the caller pass a
    project-local thresholds YAML — by default postflight uses the
    repo-shipped library. ``project_overrides_path`` (PRD-07 Phase 4)
    is an optional overlay; when present, its leaves override the
    library — including the tiebreaker tolerance under
    ``compare_runs.tie_tol_continuity_magnitude``.

    ``override_version`` lets the caller force a comparison through
    even when the two runs report incompatible SWMM solver versions.
    The default is to refuse with ``verdict="incomparable"`` and
    leave the diffs empty so a downstream script does not mistake
    solver-behaviour deltas for parameter-change deltas.

    ``parametric_store`` is an optional path to ``parametric_memory.jsonl``
    used as a fallback when the .rpt's experiment_provenance.json lacks
    a ``swmm_version`` field. ``None`` skips the fallback.

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

    # Resolve SWMM solver versions before any expensive work so a
    # cross-version refusal short-circuits cleanly. ``None`` is the
    # explicit "we could not find a version" signal.
    swmm_version_a = _read_swmm_version_for_run(run_dir_a, run_a_id, parametric_store)
    swmm_version_b = _read_swmm_version_for_run(run_dir_b, run_b_id, parametric_store)
    version_verdict = check_swmm_versions_for_compare(
        swmm_version_a, swmm_version_b
    )

    notes: list[str] = []
    # When *both* versions are missing/unknown, skip the solver-version
    # gate entirely — the user has no real cross-version risk to refuse
    # on, and the downstream "no metrics" branch already explains why
    # the comparison cannot proceed. The gate fires only when at least
    # one side carries a parseable version, which is when a real
    # version mismatch is possible.
    versions_both_unknown = (
        swmm_version_a in (None, "") and swmm_version_b in (None, "")
    )
    if (
        not version_verdict.ok
        and not override_version
        and not versions_both_unknown
    ):
        notes.append(f"solver_version_mismatch: {version_verdict.reason}")
        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metric_diffs={},
            verdict="incomparable",
            notes=notes,
        )
    if not version_verdict.ok and override_version and not versions_both_unknown:
        notes.append(
            f"solver_version_mismatch: {version_verdict.reason}"
        )
        notes.append("user override accepted")
    elif (
        version_verdict.ok
        and version_verdict.version_a != version_verdict.version_b
    ):
        # ``ok=True`` with different labels means the same-minor advisory
        # branch fired. Surface it but do not gate the diffs.
        notes.append(f"solver_version_advisory: {version_verdict.reason}")

    report_a = postflight_qa(
        run_dir_a,
        benchmarks_path=benchmarks_path,
        project_overrides_path=project_overrides_path,
    )
    report_b = postflight_qa(
        run_dir_b,
        benchmarks_path=benchmarks_path,
        project_overrides_path=project_overrides_path,
    )

    # Resolve the tiebreaker tolerance through the same overlay path
    # so a project can loosen the floor for coarse-precision .rpts.
    tie_tol = float(
        resolve_threshold(
            _TIE_TOL_DOTTED_KEY,
            reference_benchmarks_path=benchmarks_path,
            project_overrides_path=project_overrides_path,
            default=_TIE_TOL_DEFAULT,
        )
    )

    diffs = _build_metric_diffs(report_a, report_b, metric_set)

    # Per-element diffs. Read each run's .rpt text once and run both
    # parsers against the same body. A missing .rpt or missing section
    # is benign — the per-element dicts simply stay empty.
    rpt_text_a = _find_rpt_text(run_dir_a) or ""
    rpt_text_b = _find_rpt_text(run_dir_b) or ""
    node_peaks_a = parse_node_peaks_from_rpt(rpt_text_a) if rpt_text_a else {}
    node_peaks_b = parse_node_peaks_from_rpt(rpt_text_b) if rpt_text_b else {}
    sub_runoff_a = parse_subcatch_runoff_from_rpt(rpt_text_a) if rpt_text_a else {}
    sub_runoff_b = parse_subcatch_runoff_from_rpt(rpt_text_b) if rpt_text_b else {}

    node_peak_diffs = _build_node_peak_diffs(node_peaks_a, node_peaks_b)
    subcatch_runoff_diffs = _build_subcatch_runoff_diffs(sub_runoff_a, sub_runoff_b)
    top_nodes = _rank_top_movers(node_peak_diffs)
    top_subs = _rank_top_movers(subcatch_runoff_diffs)

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
            node_peak_diffs=node_peak_diffs,
            subcatch_runoff_diffs=subcatch_runoff_diffs,
            top_movers_nodes=top_nodes,
            top_movers_subcatches=top_subs,
        )
    if not _has_metrics(report_a, metric_set):
        notes.append(f"run A ({run_a_id}) has no parseable QA metrics")
        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metric_diffs=diffs,
            verdict="incomparable",
            notes=notes,
            node_peak_diffs=node_peak_diffs,
            subcatch_runoff_diffs=subcatch_runoff_diffs,
            top_movers_nodes=top_nodes,
            top_movers_subcatches=top_subs,
        )
    if not _has_metrics(report_b, metric_set):
        notes.append(f"run B ({run_b_id}) has no parseable QA metrics")
        return RunComparison(
            run_a_id=run_a_id,
            run_b_id=run_b_id,
            metric_diffs=diffs,
            verdict="incomparable",
            notes=notes,
            node_peak_diffs=node_peak_diffs,
            subcatch_runoff_diffs=subcatch_runoff_diffs,
            top_movers_nodes=top_nodes,
            top_movers_subcatches=top_subs,
        )

    verdict, verdict_notes = _decide_verdict(
        diffs, report_a, report_b, metric_set, tie_tol=tie_tol
    )
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
        node_peak_diffs=node_peak_diffs,
        subcatch_runoff_diffs=subcatch_runoff_diffs,
        top_movers_nodes=top_nodes,
        top_movers_subcatches=top_subs,
    )


def render_comparison_table(
    comparison: RunComparison,
    *,
    show_per_node: bool = False,
    show_per_subcatch: bool = False,
    top_movers_limit: int = 3,
) -> str:
    """Return a plain-text table for ``aiswmm compare`` default output.

    Default render: aggregate continuity table + a top-N nodes block +
    a top-N subcatches block (when those collections are non-empty).
    ``show_per_node`` / ``show_per_subcatch`` expand the full per-
    element tables. ``top_movers_limit`` caps the top-N blocks; pass
    a larger number for an expanded readout.

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

    # Round 3: top-mover blocks. Always rendered when the underlying
    # rankings are non-empty; gives the modeler a "what moved" view at
    # the bottom of the default table without forcing a flag.
    if comparison.top_movers_nodes:
        lines.append("")
        lines.append(
            f"Top {min(top_movers_limit, len(comparison.top_movers_nodes))} "
            "nodes that moved most (by |delta_pct| of max_total_inflow):"
        )
        for name, pct in comparison.top_movers_nodes[:top_movers_limit]:
            lines.append(f"  {name:<28} {pct:+.2f}%")
    if comparison.top_movers_subcatches:
        lines.append("")
        lines.append(
            f"Top {min(top_movers_limit, len(comparison.top_movers_subcatches))} "
            "subcatches that moved most (by |delta_pct| of total_runoff_mm):"
        )
        for name, pct in comparison.top_movers_subcatches[:top_movers_limit]:
            lines.append(f"  {name:<28} {pct:+.2f}%")

    if show_per_node and comparison.node_peak_diffs:
        lines.append("")
        lines.append("Per-node peak inflow:")
        node_header = (
            f"{'node':<28}{'max_total_a':>14}{'max_total_b':>14}"
            f"{'delta_abs':>12}{'delta_pct':>10}{'time_shift_min':>16}"
        )
        lines.append(node_header)
        lines.append("-" * len(node_header))
        for name in sorted(comparison.node_peak_diffs):
            d = comparison.node_peak_diffs[name]
            va = (
                "n/a"
                if not d.peak_a or d.peak_a.max_total_inflow is None
                else f"{d.peak_a.max_total_inflow:.3f}"
            )
            vb = (
                "n/a"
                if not d.peak_b or d.peak_b.max_total_inflow is None
                else f"{d.peak_b.max_total_inflow:.3f}"
            )
            da = (
                "n/a"
                if d.delta_max_total_inflow is None
                else f"{d.delta_max_total_inflow:+.3f}"
            )
            dp = "n/a" if d.delta_pct is None else f"{d.delta_pct:+.2f}%"
            ts = "n/a" if d.time_shift_min is None else f"{d.time_shift_min:+.1f}"
            lines.append(
                f"{name:<28}{va:>14}{vb:>14}{da:>12}{dp:>10}{ts:>16}"
            )

    if show_per_subcatch and comparison.subcatch_runoff_diffs:
        lines.append("")
        lines.append("Per-subcatch runoff (mm):")
        sub_header = (
            f"{'subcatch':<28}{'runoff_mm_a':>14}{'runoff_mm_b':>14}"
            f"{'delta_abs':>12}{'delta_pct':>10}"
        )
        lines.append(sub_header)
        lines.append("-" * len(sub_header))
        for name in sorted(comparison.subcatch_runoff_diffs):
            d = comparison.subcatch_runoff_diffs[name]
            va = (
                "n/a"
                if not d.runoff_a or d.runoff_a.total_runoff_mm is None
                else f"{d.runoff_a.total_runoff_mm:.3f}"
            )
            vb = (
                "n/a"
                if not d.runoff_b or d.runoff_b.total_runoff_mm is None
                else f"{d.runoff_b.total_runoff_mm:.3f}"
            )
            da = (
                "n/a"
                if d.delta_total_runoff_mm is None
                else f"{d.delta_total_runoff_mm:+.3f}"
            )
            dp = "n/a" if d.delta_pct is None else f"{d.delta_pct:+.2f}%"
            lines.append(f"{name:<28}{va:>14}{vb:>14}{da:>12}{dp:>10}")

    if comparison.notes:
        lines.append("")
        lines.append("notes:")
        for note in comparison.notes:
            lines.append(f"  - {note}")
    return "\n".join(lines)
