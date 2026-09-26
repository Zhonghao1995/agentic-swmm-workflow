"""Audit-end auto-trigger hook (PRD M2 + M6 + M7.4).

After a successful audit pipeline run, this module decides whether the
run is eligible for memory (:func:`is_skip_memory_run`) and, when it is,
runs the ``_REFRESH_PHASES`` pipeline: a parametric row, a ``runs`` row,
a calibration row when provenance has one, a negative lesson on a
continuity FAIL, and the outcome ledger. Every phase writes a ledger or
a table in the store; nothing regenerates a document (the lessons file,
its decay pass, the memory MOC and the RAG corpus were retired in the
memory simplification, 2026-09-26).

Pure-function callable so the audit command and the planner can reuse
the same trigger logic in tests without spawning the real audit
subprocess.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from agentic_swmm.memory.jsonl_store import append_row
from dataclasses import dataclass
from typing import Any, Callable


_AGENT_DIR_RE = re.compile(r"(^|/)agent-[A-Za-z0-9_-]+$")
_SKIP_CATEGORIES = {"acceptance", "ci", "benchmark-smoke"}


def _read_provenance(run_dir: Path) -> dict[str, Any]:
    for relative in ("09_audit/experiment_provenance.json", "experiment_provenance.json"):
        candidate = run_dir / relative
        if candidate.is_file():
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            return payload if isinstance(payload, dict) else {}
    return {}


def is_skip_memory_run(run_dir: Path) -> tuple[bool, str]:
    """Return ``(skip, reason)`` for ``run_dir``.

    Skip conditions (any one is enough):
    - ``AISWMM_SKIP_MEMORY=1`` in the environment.
    - ``experiment_provenance.json`` carries ``category`` in
      ``{acceptance, ci, benchmark-smoke}``.
    - The run dir is under ``runs/acceptance/`` or ``runs/.archive/``.
    - The run dir matches ``runs/agent/agent-*/``.
    """
    if os.environ.get("AISWMM_SKIP_MEMORY", "").strip() in {"1", "true", "True", "yes"}:
        return True, "AISWMM_SKIP_MEMORY env var set"

    provenance = _read_provenance(run_dir)
    category = str(provenance.get("category", "")).strip().lower()
    if category in _SKIP_CATEGORIES:
        return True, f"provenance category={category}"

    resolved = run_dir.resolve()
    parts = resolved.parts
    # Check EVERY runs/ component for an adjacent acceptance/, not just
    # the first: ``parts.index("runs")`` anchored the first occurrence,
    # so a path with an unrelated ancestor named "runs" (e.g.
    # .../runs/archive/.../runs/acceptance/<case>) tested the wrong
    # pair and let acceptance-run data leak into long-term memory
    # (found 2026-08-08, reproduced).
    if any(
        part == "runs" and idx + 1 < len(parts) and parts[idx + 1] == "acceptance"
        for idx, part in enumerate(parts)
    ):
        return True, "run path under runs/acceptance/"
    if ".archive" in parts:
        return True, "run path under runs/.archive/"

    posix = resolved.as_posix()
    if "/runs/agent/" in posix and _AGENT_DIR_RE.search(posix):
        return True, "run path matches runs/agent/agent-*/"

    return False, ""


def _append_skip_log(memory_dir: Path, run_dir: Path, reason: str) -> None:
    skip_log = memory_dir / ".skip_log.jsonl"
    memory_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_dir": str(run_dir),
        "reason": reason,
        "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    append_row(skip_log, entry, sort_keys=False)


def _resolve_memory_dir(project_root: Path | None = None) -> Path:
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override)
    if project_root is not None:
        return project_root / "memory" / "store"
    # Workspace-anchored, never bare cwd-relative (finding F-15, 2026-09-02).
    from agentic_swmm.utils.paths import resolve_memory_dir

    return resolve_memory_dir()


def _project_root_for(runs_dir: Path) -> Path:
    """Return the project root that owns ``runs_dir``.

    The convention is that ``runs_dir`` is ``<project_root>/runs`` (or
    a deeper subdirectory). The project root is the parent of the
    first ``runs`` ancestor in the path, so audit hooks landing in a
    tmpdir during tests do not accidentally write to the live
    repo's memory dir.
    """
    resolved = runs_dir.resolve()
    if resolved.name == "runs":
        return resolved.parent
    for parent in resolved.parents:
        if parent.name == "runs":
            return parent.parent
    return resolved.parent


def _resolve_runs_dir(run_dir: Path) -> Path:
    override = os.environ.get("AISWMM_RUNS_ROOT")
    if override:
        return Path(override)
    for parent in run_dir.parents:
        if parent.name == "runs":
            return parent
    return run_dir.parent


def _record_parametric_from_provenance(
    *, run_dir: Path, memory_dir: Path
) -> str | None:
    """Append a parametric_memory row for ``run_dir`` if provenance exists.

    PRD-06 Phase A.5: every successful, non-skipped run gets one JSONL
    line describing its quantitative fingerprint. We pull only fields
    the audit pipeline already writes — this hook is a *bridge*, not
    a new source of truth.

    PRD-06 Phase C §15 — when an outer :class:`CalibrationBatch` has
    flagged ``AISWMM_IN_CALIBRATION_BATCH=1`` we *do not* write the
    per-run row; the batch flushes one consolidated row at the end.

    Returns the path to the written JSONL, or ``None`` when nothing
    was written (no provenance, missing required fields, write error,
    or in-batch suppression). Failures are soft: a broken parametric
    write must not block the rest of the memory refresh.
    """
    from agentic_swmm.agent.calibration_batch import is_batch_active
    from agentic_swmm.memory.parametric_memory import (
        ParametricRecord,
        record_parametric_run,
    )

    if is_batch_active():
        # Calibration batch is active — suppress the per-iteration write.
        # The batch's __exit__ flushes one consolidated record.
        return None

    provenance = _read_provenance(run_dir)
    if not provenance:
        return None

    run_id = str(provenance.get("run_id") or "").strip()
    case_name = str(provenance.get("case_name") or "").strip()
    if not run_id or not case_name:
        return None

    tools = provenance.get("tools") or {}
    swmm_version = tools.get("swmm5_version") or tools.get("swmm_version")

    # Continuity values live under metrics.continuity_error.values in the
    # v1.1 audit schema. audit_run.py writes the keys ``runoff_quantity`` /
    # ``flow_routing`` (verified against a live tecnopolo provenance); older
    # fixtures used ``runoff`` / ``flow``, so accept both. Reading the wrong
    # key was why qa_metrics was always empty.
    metrics = provenance.get("metrics") or {}
    continuity = (metrics.get("continuity_error") or {}).get("values") or {}
    qa_metrics: dict[str, Any] = {}
    runoff_val = continuity.get("runoff_quantity", continuity.get("runoff"))
    flow_val = continuity.get("flow_routing", continuity.get("flow"))
    if runoff_val is not None:
        try:
            qa_metrics["runoff_continuity_pct"] = float(runoff_val)
        except (TypeError, ValueError):
            pass
    if flow_val is not None:
        try:
            qa_metrics["flow_continuity_pct"] = float(flow_val)
        except (TypeError, ValueError):
            pass

    # Peak flow is the run's primary hydraulic fingerprint — capture it as
    # flat, queryable fields when the audit actually parsed a value (it is
    # ``null`` when the target node could not be matched).
    peak = metrics.get("peak_flow") or {}
    peak_value = peak.get("value")
    if peak_value is not None:
        try:
            qa_metrics["peak_flow_value"] = float(peak_value)
            if peak.get("node"):
                qa_metrics["peak_flow_node"] = str(peak["node"])
            if peak.get("unit"):
                qa_metrics["peak_flow_unit"] = str(peak["unit"])
            if peak.get("time_hhmm"):
                qa_metrics["peak_flow_time_hhmm"] = str(peak["time_hhmm"])
        except (TypeError, ValueError):
            pass

    workflow_mode = provenance.get("workflow_mode")
    model_structure: dict[str, Any] = {}
    if workflow_mode:
        model_structure["workflow_mode"] = workflow_mode

    # Round 5 / PRD-06 §4.1: surface watershed_classification and
    # performance_metrics from provenance when present. Upstream may
    # or may not populate these (depends on workflow mode); when
    # absent the record's defaults keep them as empty dicts.
    watershed_classification = provenance.get("watershed_classification")
    if not isinstance(watershed_classification, dict):
        watershed_classification = {}

    performance_metrics_in = provenance.get("performance_metrics")
    if not isinstance(performance_metrics_in, dict):
        performance_metrics_in = {}

    # calibration_status / parameter_set_ref: passed through verbatim
    # when the provenance carries them, validated against the allowed
    # set by ``record_parametric_run`` itself.
    calibration_block = provenance.get("calibration") or {}
    calibration_status_in = provenance.get("calibration_status")
    if calibration_status_in is None and isinstance(calibration_block, dict):
        calibration_status_in = calibration_block.get("status")
    if calibration_status_in is not None:
        calibration_status_in = str(calibration_status_in)

    parameter_set_ref_in = provenance.get("parameter_set_ref")
    if parameter_set_ref_in is None and isinstance(calibration_block, dict):
        parameter_set_ref_in = calibration_block.get("parameter_set_ref")
    if parameter_set_ref_in is not None:
        parameter_set_ref_in = str(parameter_set_ref_in)

    # ``evidence_runs_count`` defaults to 1 per the dataclass; only the
    # CalibrationBatch flush overrides it when consolidating iterations.
    evidence_runs_in = provenance.get("evidence_runs_count")
    try:
        evidence_runs_count = int(evidence_runs_in) if evidence_runs_in is not None else 1
    except (TypeError, ValueError):
        evidence_runs_count = 1
    if evidence_runs_count < 1:
        evidence_runs_count = 1

    record = ParametricRecord(
        run_id=run_id,
        case_name=case_name,
        swmm_version=str(swmm_version) if swmm_version else None,
        model_structure=model_structure,
        qa_metrics=qa_metrics,
        performance_metrics=performance_metrics_in,
        watershed_classification=watershed_classification,
        calibration_status=calibration_status_in
        if calibration_status_in in {
            "uncalibrated",
            "calibrated_against_observed",
            "validation_only",
        }
        else None,
        parameter_set_ref=parameter_set_ref_in,
        evidence_runs_count=evidence_runs_count,
    )

    store_path = memory_dir / "parametric_memory.jsonl"
    try:
        record_parametric_run(store_path, record)
    except (ValueError, OSError):
        return None
    return str(store_path)


def _record_negative_lesson_for_continuity_fail(
    *, run_dir: Path, memory_dir: Path
) -> str | None:
    """Bridge audit -> negative_lessons when continuity classifies FAIL.

    PRD-06 Phase C.2 integration: a run that posts continuity values
    above the FAIL band leaves a parametric record (the parametric
    bridge ran first). When that record exists AND continuity is in
    the FAIL band, also write a negative_lesson so the agent will not
    re-propose the same parameter region next time.

    The "FAIL band" thresholds follow the same conservative library
    fallbacks the runtime gate uses (``postflight.py``): runoff
    continuity above 10% magnitude, or flow continuity above 5%. We
    keep them in-line rather than re-importing the YAML resolver so a
    broken benchmarks file never blocks the lesson record.

    Returns the negative-lessons store path on success, ``None`` when
    no lesson was written (no provenance, no continuity values, not in
    FAIL band, missing required fields, write error). Soft-fail
    everywhere — same contract as the parametric / calibration bridges.
    """
    from agentic_swmm.memory.negative_lessons import (
        NegativeLesson,
        record_negative_lesson,
    )

    provenance = _read_provenance(run_dir)
    if not provenance:
        return None

    run_id = str(provenance.get("run_id") or "").strip()
    case_name = str(provenance.get("case_name") or "").strip()
    if not run_id or not case_name:
        return None

    metrics = provenance.get("metrics") or {}
    continuity = (metrics.get("continuity_error") or {}).get("values") or {}
    metric_observed: dict[str, float] = {}
    fail_codes: list[str] = []
    for key, threshold in (("runoff", 10.0), ("flow", 5.0)):
        if key not in continuity:
            continue
        try:
            value = float(continuity[key])
        except (TypeError, ValueError):
            continue
        metric_observed[f"{key}_continuity_pct"] = value
        if abs(value) >= threshold:
            fail_codes.append(f"{key}_continuity_pct")

    if not fail_codes:
        # PASS / WARN — nothing for the negative-lessons store.
        return None

    # Parameters tried: pull whatever the calibration block or the
    # provenance ``parameters`` block carries. A FAIL on an un-tuned
    # run still records the metric so the next caller can still spot
    # the case-level pattern even without a parameter set.
    parameters_tried: dict[str, float] = {}
    calibration = provenance.get("calibration") or {}
    if isinstance(calibration, dict):
        for name, value in (calibration.get("parameters") or {}).items():
            try:
                parameters_tried[str(name)] = float(value)
            except (TypeError, ValueError):
                continue
    if not parameters_tried:
        for name, value in (provenance.get("parameters") or {}).items():
            try:
                parameters_tried[str(name)] = float(value)
            except (TypeError, ValueError):
                continue

    lesson = NegativeLesson(
        run_id=run_id,
        case_name=case_name,
        lesson_type="continuity_fail",
        parameters_tried=parameters_tried,
        metric_observed=metric_observed,
        note=f"postflight FAIL on {', '.join(sorted(fail_codes))}",
    )

    jsonl_path = memory_dir / "negative_lessons.jsonl"
    try:
        record_negative_lesson(jsonl_path, lesson)
    except (ValueError, OSError):
        return None
    return str(jsonl_path)


def _record_calibration_from_provenance(
    *, run_dir: Path, memory_dir: Path
) -> str | None:
    """Append a calibration_memory row when provenance has a ``calibration`` block.

    PRD-06 Phase B.3 bridge: SCE-UA / DREAM-ZS runs land a structured
    block in ``experiment_provenance.json``. When present, mirror it
    into the JSONL store so the agent can answer "best Manning's *n*
    for case X in the last 6 months" without rescanning run dirs.

    Returns the path to the written JSONL, or ``None`` when nothing
    was written (no provenance, no calibration block, missing required
    fields, write error). Soft-fail: a broken write must not block the
    rest of the memory pipeline — same contract as the parametric
    bridge above.
    """
    from agentic_swmm.memory.calibration_memory import (
        CalibrationRecord,
        record_calibration_run,
    )

    provenance = _read_provenance(run_dir)
    if not provenance:
        return None

    calibration = provenance.get("calibration")
    if not isinstance(calibration, dict) or not calibration:
        # No calibration block — silently skip (matches the PRD spec
        # for non-calibration runs).
        return None

    run_id = str(provenance.get("run_id") or "").strip()
    case_name = str(provenance.get("case_name") or "").strip()
    if not run_id or not case_name:
        return None

    tools = provenance.get("tools") or {}
    swmm5_version = tools.get("swmm5_version") or tools.get("swmm_version")

    parameters_raw = calibration.get("parameters") or {}
    parameters: dict[str, float] = {}
    if isinstance(parameters_raw, dict):
        for name, value in parameters_raw.items():
            try:
                parameters[str(name)] = float(value)
            except (TypeError, ValueError):
                continue

    secondary_raw = calibration.get("secondary_metrics") or {}
    secondary: dict[str, float] = {}
    if isinstance(secondary_raw, dict):
        for name, value in secondary_raw.items():
            try:
                secondary[str(name)] = float(value)
            except (TypeError, ValueError):
                continue

    objective_value: float | None = None
    raw_obj = calibration.get("objective_value")
    if raw_obj is not None:
        try:
            objective_value = float(raw_obj)
        except (TypeError, ValueError):
            objective_value = None

    n_evaluations: int | None = None
    raw_n = calibration.get("n_evaluations")
    if raw_n is not None:
        try:
            n_evaluations = int(raw_n)
        except (TypeError, ValueError):
            n_evaluations = None

    wall_time_s: float | None = None
    raw_wall = calibration.get("wall_time_s")
    if raw_wall is not None:
        try:
            wall_time_s = float(raw_wall)
        except (TypeError, ValueError):
            wall_time_s = None

    record = CalibrationRecord(
        run_id=run_id,
        case_name=case_name,
        use_case=calibration.get("use_case"),
        algorithm=calibration.get("algorithm"),
        parameters=parameters,
        objective_name=calibration.get("objective_name"),
        objective_value=objective_value,
        secondary_metrics=secondary,
        swmm5_version=str(swmm5_version) if swmm5_version else None,
        n_evaluations=n_evaluations,
        wall_time_s=wall_time_s,
    )

    store_path = memory_dir / "calibration_memory.jsonl"
    try:
        record_calibration_run(store_path, record)
    except (ValueError, OSError):
        return None
    return str(store_path)


def _emit_audit_memory_trace(
    *, run_dir: Path, memory_dir: Path, parametric_path: Path
) -> Path | None:
    """Write the audit-hook's memory_trace.jsonl line.

    PRD-07 Phase 2 seed wire-up: every parametric_memory append also
    leaves a transparency line in ``<run_dir>/memory_trace.jsonl``.
    The line records the case the hook just observed and the count of
    prior runs visible at that moment — enough for the user, reading
    the run dir three months later, to see why memory grew.

    Returns the trace path on success or ``None`` if the run dir is
    not writable / provenance fields are missing. Either way, an
    exception never propagates: the caller wraps this in
    ``try/except`` for total isolation.
    """
    from agentic_swmm.agent.memory_context import gather_memory_context
    from agentic_swmm.agent.memory_trace import log_memory_decision

    provenance = _read_provenance(run_dir)
    case_name = str(provenance.get("case_name") or "").strip()
    if not case_name:
        return None

    # The trace records the *pre-write* view of memory: at this point
    # we have just appended ``parametric_path`` so we want the count
    # the user would have seen if they'd consulted the store before
    # the hook fired. Gather first, then log.
    context = gather_memory_context(
        memory_dir=memory_dir,
        case_name=case_name,
        metrics_of_interest=("runoff_continuity_pct", "flow_continuity_pct"),
    )

    return log_memory_decision(
        run_dir=run_dir,
        decision_point="audit_hook_parametric_write",
        context=context,
        decision="recorded",
        confidence="auto_complete",
    )


@dataclass
class _RefreshContext:
    """Shared state for the refresh phases.

    ``result`` is the exact dict ``trigger_memory_refresh`` returns —
    each phase writes its keys into it and appends its error strings to
    ``result["errors"]``, preserving the pre-pipeline calling
    convention. TODO(#207): boundary-migrate — every phase appends a
    per-site message to ``result["errors"]`` rather than returning a
    constant default, so the fail-soft decorator's "return ``default``"
    contract does not fit yet; migrating would first have to rework
    this result-collection convention.
    """

    run_dir: Path
    runs_dir: Path
    project_root: Path
    memory_dir: Path
    result: dict[str, Any]


def _phase_parametric_bridge(ctx: _RefreshContext) -> None:
    # PRD-06 Phase A.5: bridge audit -> parametric_memory. Pull only
    # what experiment_provenance.json already records; soft failures.
    try:
        parametric_path = _record_parametric_from_provenance(
            run_dir=ctx.run_dir, memory_dir=ctx.memory_dir
        )
        if parametric_path:
            ctx.result["parametric_memory"] = parametric_path
            # PRD-07 Phase 2: every parametric write also leaves an
            # auditable transparency line in the run dir. The trace
            # is the user-visible counterpart to the JSONL store and
            # the seed wire-up for the disambiguator / QA replacement
            # call sites the next phase introduces.
            try:
                trace_path = _emit_audit_memory_trace(
                    run_dir=ctx.run_dir,
                    memory_dir=ctx.memory_dir,
                    parametric_path=Path(parametric_path),
                )
                if trace_path:
                    ctx.result["memory_trace"] = str(trace_path)
            except Exception as exc:  # noqa: BLE001 — never block the pipeline
                ctx.result["errors"].append(f"memory trace write failed: {exc}")
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        ctx.result["errors"].append(f"parametric memory write failed: {exc}")


def _phase_runs_row(ctx: _RefreshContext) -> None:
    """One row per audited run in the store's database (memory simplification
    part 2, 2026-09-06): the ``runs`` table replaces the generated index
    files as the answer to "what has been run for this case"."""
    try:
        from agentic_swmm.memory.store import record_run

        row = record_run(ctx.memory_dir, ctx.run_dir)
        if row:
            ctx.result["runs_row"] = row["run_id"]
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        ctx.result["errors"].append(f"runs row write failed: {exc}")


def _phase_calibration_bridge(ctx: _RefreshContext) -> None:
    # PRD-06 Phase B.3: bridge audit -> calibration_memory. Only fires
    # when provenance carries a ``calibration`` block; non-calibration
    # runs are silently skipped. Soft failures.
    try:
        calibration_path = _record_calibration_from_provenance(
            run_dir=ctx.run_dir, memory_dir=ctx.memory_dir
        )
        if calibration_path:
            ctx.result["calibration_memory"] = calibration_path
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        ctx.result["errors"].append(f"calibration memory write failed: {exc}")


def _phase_negative_lessons(ctx: _RefreshContext) -> None:
    # PRD-06 Phase C.2: bridge audit -> negative_lessons when continuity
    # classifies FAIL AND the parametric bridge already produced a row.
    # The parametric record is the eligibility marker: a run that never
    # made it into parametric_memory should not seed a negative lesson.
    # Soft-fail: any write error never blocks the audit.
    if not ctx.result.get("parametric_memory"):
        return
    try:
        negative_path = _record_negative_lesson_for_continuity_fail(
            run_dir=ctx.run_dir, memory_dir=ctx.memory_dir
        )
        if negative_path:
            ctx.result["negative_lessons"] = negative_path
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        ctx.result["errors"].append(f"negative lesson write failed: {exc}")


def _phase_outcome_ledger(ctx: _RefreshContext) -> None:
    # PR-3 Phase 1: append outcome events to the application outcome ledger.
    # Fires after the parametric/calibration bridges so provenance is complete.
    # Soft-fail: a broken write must never block the rest of the pipeline.
    # Skipped when AISWMM_SKIP_MEMORY=1 (is_skip_memory_run would have
    # short-circuited above, but guard here too for defensive clarity).
    try:
        from agentic_swmm.memory.memory_outcomes import (
            OUTCOME_LEDGER_FILENAME,
            classify_and_record_outcome,
        )

        outcome_store = ctx.memory_dir / OUTCOME_LEDGER_FILENAME
        provenance_for_outcomes = _read_provenance(ctx.run_dir)
        if provenance_for_outcomes.get("memories_applied") is not None:
            # Look for the manifest in the run dir (standard layout).
            manifest_candidate = ctx.run_dir / "manifest.json"
            manifest_path = manifest_candidate if manifest_candidate.is_file() else None
            event_ids = classify_and_record_outcome(
                run_dir=ctx.run_dir,
                provenance=provenance_for_outcomes,
                manifest_path=manifest_path,
                memory_dir=ctx.memory_dir,
                store_path=outcome_store,
            )
            if event_ids:
                ctx.result["outcome_events"] = event_ids
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        ctx.result["errors"].append(f"outcome log write failed: {exc}")


# Ordered pipeline. Order is behaviour: the parametric bridge precedes
# negative-lessons (eligibility marker) and the outcome ledger (complete
# provenance). Each phase is fail-soft in isolation — one phase's
# exception never blocks the next.
_REFRESH_PHASES: tuple[Callable[[_RefreshContext], None], ...] = (
    _phase_parametric_bridge,
    _phase_runs_row,
    _phase_calibration_bridge,
    _phase_negative_lessons,
    _phase_outcome_ledger,
)


def trigger_memory_refresh(
    run_dir: Path,
    *,
    no_memory: bool = False,
) -> dict[str, Any]:
    """Run the audit -> memory hook for ``run_dir``.

    Gating and context construction happen here; the memory-bridge work
    itself runs as the ordered ``_REFRESH_PHASES`` pipeline, each phase
    independently fail-soft. Returns a dict describing what happened:
    ``{"skipped": bool, "reason": str, "errors": list[str]}`` plus the
    per-phase keys (``parametric_memory``, ``runs_row``,
    ``calibration_memory``, ``negative_lessons``, ``outcome_events``).
    """
    result: dict[str, Any] = {
        "skipped": False,
        "reason": "",
        "errors": [],
    }
    if no_memory:
        result["skipped"] = True
        result["reason"] = "--no-memory flag set"
        return result

    skip, reason = is_skip_memory_run(run_dir)
    runs_dir = _resolve_runs_dir(run_dir)
    project_root = _project_root_for(runs_dir)
    memory_dir = _resolve_memory_dir(project_root)
    if skip:
        _append_skip_log(memory_dir, run_dir, reason)
        result["skipped"] = True
        result["reason"] = reason
        return result

    memory_dir.mkdir(parents=True, exist_ok=True)
    ctx = _RefreshContext(
        run_dir=run_dir,
        runs_dir=runs_dir,
        project_root=project_root,
        memory_dir=memory_dir,
        result=result,
    )
    for phase in _REFRESH_PHASES:
        phase(ctx)
    return result
