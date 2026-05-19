"""Memory read interface for the agent runtime (PRD-07 Phase 1).

The agent should not greet a returning user as if it had no history of
their work. This module exposes a single function,
:func:`gather_memory_context`, that returns a typed snapshot of
relevant prior runs plus the thresholds the agent will compare them
against. It is **read-only** — nothing in this module mutates the
memory store on disk.

Why read interface (not a decision engine)
------------------------------------------
PRD-07 Phase 1 is deliberately scoped to the *read* side. Phases 3-4
introduce decision policy (memory-informed disambiguation, QA
threshold replacement). Splitting the two prevents the runtime from
acquiring decision logic before the data it reads is well-tested.

Failure mode
------------
:func:`gather_memory_context` always returns a populated
:class:`MemoryContext`. Missing memory dirs, missing files, malformed
YAML — every failure path yields an empty-field context rather than
an exception. Downstream callers branch on ``parametric_hits == []``
and ``reference_thresholds.get(metric, {})``; they never have to wrap
this call in ``try/except``.

Relationship to ``agentic_swmm.memory.parametric_memory``
---------------------------------------------------------
``parametric_memory`` is the *store*. This module is the *adapter*
that the agent runtime calls before making a decision. It does not
re-implement filtering; it forwards to ``recall_parametric`` and
re-shapes rows into the frozen :class:`ParametricRecord` dataclass
declared here so the agent has a stable read-side type (the stored
record is also frozen but its dict subfields are mutable; the agent
should not be able to scribble on the store's row objects either way).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agentic_swmm.agent.feature_flags import memory_informed_disabled
from agentic_swmm.memory.benchmark_resolver import (
    PROJECT_OVERRIDES_FILENAME,
    resolve_threshold,
)
from agentic_swmm.memory.parametric_memory import recall_parametric


@dataclass(frozen=True)
class ParametricRecord:
    """Read-side view of one parametric memory row.

    Mirrors the writer's :class:`agentic_swmm.memory.parametric_memory.ParametricRecord`
    but lives in the agent namespace so callers do not import the writer
    just to type a read result. The frozen-dataclass shape gives the
    runtime stable attribute access without depending on whether the
    on-disk schema gains additional optional fields in Phase B+ (those
    land in :attr:`extras`).
    """

    run_id: str
    case_name: str
    swmm_version: str | None = None
    model_structure: dict[str, Any] = field(default_factory=dict)
    qa_metrics: dict[str, Any] = field(default_factory=dict)
    performance_metrics: dict[str, Any] = field(default_factory=dict)
    watershed_classification: dict[str, Any] = field(default_factory=dict)
    recorded_utc: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ParametricRecord":
        """Project a stored JSON row into the read-side dataclass.

        Unknown fields land in :attr:`extras` so a schema bump in the
        writer does not silently drop information the runtime might
        want to read.
        """
        known = {
            "schema_version",
            "run_id",
            "case_name",
            "swmm_version",
            "model_structure",
            "qa_metrics",
            "performance_metrics",
            "watershed_classification",
            "recorded_utc",
            "calibration_status",
            "parameter_set_ref",
        }
        extras = {k: v for k, v in row.items() if k not in known}
        return cls(
            run_id=str(row.get("run_id") or ""),
            case_name=str(row.get("case_name") or ""),
            swmm_version=row.get("swmm_version"),
            model_structure=dict(row.get("model_structure") or {}),
            qa_metrics=dict(row.get("qa_metrics") or {}),
            performance_metrics=dict(row.get("performance_metrics") or {}),
            watershed_classification=dict(
                row.get("watershed_classification") or {}
            ),
            recorded_utc=row.get("recorded_utc"),
            extras=extras,
        )


@dataclass
class MemoryContext:
    """One read-side snapshot of the memory the agent has access to.

    The fields are mutable on purpose: the runtime can append a few
    helper-derived flags (e.g. a downstream component might pin
    "consensus_evident" on the same context) without forcing every
    consumer to construct a fresh dataclass. The store itself is never
    mutated through this object — these fields are local copies.
    """

    parametric_hits: list[ParametricRecord] = field(default_factory=list)
    reference_thresholds: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def parametric_hit_count(self) -> int:
        return len(self.parametric_hits)

    def is_empty(self) -> bool:
        """Return ``True`` when nothing was found in any consulted store.

        Useful for the "first run on a brand-new case" branch where
        callers want to fall back to deterministic defaults without
        scanning each field.
        """
        return (
            not self.parametric_hits
            and not self.reference_thresholds
        )


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _format_continuity_mean(hits: list[ParametricRecord]) -> str | None:
    """Return a short "mean continuity X.XX%" fragment, or ``None``.

    Continuity is the single most-mentioned QA number in SWMM
    workflows, so the summary string carries it explicitly whenever
    enough rows have it. We require ≥1 sample to avoid printing
    statistics on the empty list.
    """
    values: list[float] = []
    for h in hits:
        v = h.qa_metrics.get("runoff_continuity_pct")
        if v is None:
            continue
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    mean = statistics.fmean(values)
    return f"mean runoff continuity {mean:.2f}%"


def _build_summary(
    *,
    case_name: str,
    hits: list[ParametricRecord],
    metrics_of_interest: tuple[str, ...],
) -> str:
    """One-line plain-English summary an LLM (or chat_note) can read.

    Designed to be ≤120 chars so it fits in audit notes without
    wrapping. Optional metric fragments append when data is present.
    """
    n = len(hits)
    parts = [f"{n} prior run{'s' if n != 1 else ''} of {case_name}"]
    if hits:
        frag = _format_continuity_mean(hits)
        if frag and (
            not metrics_of_interest
            or "runoff_continuity_pct" in metrics_of_interest
        ):
            parts.append(frag)
    return ", ".join(parts) + "."


def _gather_thresholds(
    benchmarks_path: Path,
    metrics: Iterable[str],
    *,
    project_overrides_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Prefill {metric_name: thresholds_dict} for the requested metrics.

    The mapping from a metric name to its YAML dotted-path lives here
    instead of in the YAML itself so a renamed metric never silently
    fails — adding a new metric requires editing this table, which
    forces the developer to confirm the YAML path exists.

    Resolution goes through :func:`resolve_threshold` so a project
    overlay at ``project_overrides_path`` wins over the library leaf.
    """
    METRIC_TO_PATH = {
        "runoff_continuity_pct": "continuity_thresholds_pct.runoff",
        "flow_continuity_pct": "continuity_thresholds_pct.flow",
        "mass_balance_pct": "continuity_thresholds_pct.mass_balance",
    }
    out: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        path = METRIC_TO_PATH.get(metric)
        if path is None:
            continue
        thresholds = resolve_threshold(
            path,
            reference_benchmarks_path=benchmarks_path,
            project_overrides_path=project_overrides_path,
            default=None,
        )
        if not isinstance(thresholds, dict):
            continue
        warn = thresholds.get("warn")
        fail = thresholds.get("fail")
        if warn is None and fail is None:
            # YAML leaf is the un-cited-placeholder pattern — skip so
            # callers don't see {"warn": None, "fail": None} and try
            # to compare against null.
            continue
        out[metric] = dict(thresholds)
    return out


def gather_memory_context(
    *,
    memory_dir: Path,
    case_name: str,
    use_case: str | None = None,
    metrics_of_interest: tuple[str, ...] = (),
) -> MemoryContext:
    """Return a read-only :class:`MemoryContext` for the case at hand.

    Arguments:
        memory_dir: Directory holding ``parametric_memory.jsonl`` and
            ``reference_benchmarks.yaml`` — typically
            ``<project_root>/memory/modeling-memory``.
        case_name: Project / watershed label to filter parametric rows
            against. Must match the writer's ``case_name`` exactly;
            cross-case similarity is Phase 5 work.
        use_case: Optional secondary filter on
            ``model_structure.use_case`` (e.g. ``"stormwater_event"``).
        metrics_of_interest: Names of QA metrics the caller plans to
            consult thresholds for. Empty tuple skips the threshold
            prefill — useful for callers that only want a summary.

    The function is exception-safe end-to-end:
        * Missing ``memory_dir`` → empty context with provenance.
        * Missing parametric_memory.jsonl → empty hits.
        * Missing or malformed reference_benchmarks.yaml → empty
          thresholds.

    None of these failure modes raise; the returned object always
    has the four fields populated to their natural empty value.

    When ``AISWMM_DISABLE_MEMORY_INFORMED=1`` is set in the
    environment the function short-circuits *before* any store is
    read and returns an empty :class:`MemoryContext` with a
    ``disabled`` marker in the provenance. Callers do not need to
    branch on the flag — they see the same shape as a fresh project.
    """
    memory_dir_path = Path(memory_dir)

    if memory_informed_disabled():
        return MemoryContext(
            parametric_hits=[],
            reference_thresholds={},
            summary="",
            provenance={
                "memory_dir": str(memory_dir_path),
                "case_name": case_name,
                "use_case": use_case,
                "metrics_of_interest": list(metrics_of_interest),
                "gathered_at_utc": _now_iso(),
                "schema_version": "1.0",
                "disabled": True,
                "disabled_reason": "AISWMM_DISABLE_MEMORY_INFORMED",
            },
        )

    parametric_path = memory_dir_path / "parametric_memory.jsonl"
    benchmarks_path = memory_dir_path / "reference_benchmarks.yaml"
    project_overrides_path = memory_dir_path / PROJECT_OVERRIDES_FILENAME

    filters: dict[str, Any] = {"case_name": case_name}
    if use_case:
        filters["model_structure.use_case"] = use_case

    rows = recall_parametric(parametric_path, filters)
    hits = [ParametricRecord.from_row(r) for r in rows]

    thresholds: dict[str, dict[str, Any]] = {}
    if metrics_of_interest:
        thresholds = _gather_thresholds(
            benchmarks_path,
            metrics_of_interest,
            project_overrides_path=project_overrides_path,
        )

    summary = _build_summary(
        case_name=case_name,
        hits=hits,
        metrics_of_interest=metrics_of_interest,
    )

    provenance: dict[str, Any] = {
        "memory_dir": str(memory_dir_path),
        "parametric_memory_path": str(parametric_path),
        "reference_benchmarks_path": str(benchmarks_path),
        "project_overrides_path": str(project_overrides_path),
        "parametric_hit_count": len(hits),
        "reference_threshold_count": len(thresholds),
        "case_name": case_name,
        "use_case": use_case,
        "metrics_of_interest": list(metrics_of_interest),
        "gathered_at_utc": _now_iso(),
        "schema_version": "1.0",
    }

    return MemoryContext(
        parametric_hits=hits,
        reference_thresholds=thresholds,
        summary=summary,
        provenance=provenance,
    )


__all__ = [
    "MemoryContext",
    "ParametricRecord",
    "gather_memory_context",
]
