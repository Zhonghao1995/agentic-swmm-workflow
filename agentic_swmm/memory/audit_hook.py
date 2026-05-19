"""Audit-end auto-trigger hook (PRD M2 + M6 + M7.4).

After a successful audit pipeline run, this module:

1. Decides whether the run is eligible for memory summarisation
   (:func:`is_skip_memory_run`).
2. When eligible, refreshes ``memory/modeling-memory/lessons_learned.md``
   via the existing summarise-memory CLI.
3. When ``--no-rag`` is not set, rebuilds the RAG corpus via
   ``skills/swmm-rag-memory/scripts/refresh_after_run.py``.

Pure-function callable so the audit command and the planner can reuse
the same trigger logic in tests without spawning the real audit
subprocess.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
    if "acceptance" in parts and "runs" in parts:
        runs_idx = parts.index("runs")
        if runs_idx + 1 < len(parts) and parts[runs_idx + 1] == "acceptance":
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
    with skip_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _resolve_memory_dir(project_root: Path | None = None) -> Path:
    override = os.environ.get("AISWMM_MEMORY_DIR")
    if override:
        return Path(override)
    if project_root is not None:
        return project_root / "memory" / "modeling-memory"
    return Path("memory/modeling-memory")


def _resolve_rag_dir(project_root: Path | None = None) -> Path:
    override = os.environ.get("AISWMM_RAG_DIR")
    if override:
        return Path(override)
    if project_root is not None:
        return project_root / "memory" / "rag-memory"
    return Path("memory/rag-memory")


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


def _bump_lessons_mtime(memory_dir: Path) -> Path:
    """Touch lessons_learned.md to record that a refresh happened.

    The real summariser writes new content; we always at least bump
    the mtime so callers can detect the refresh deterministically.
    """
    memory_dir.mkdir(parents=True, exist_ok=True)
    lessons_env = os.environ.get("AISWMM_LESSONS_PATH")
    lessons = Path(lessons_env) if lessons_env else (memory_dir / "lessons_learned.md")
    lessons.parent.mkdir(parents=True, exist_ok=True)
    if not lessons.exists():
        lessons.write_text("<!-- schema_version: 1.1 -->\n# Lessons\n", encoding="utf-8")
    else:
        # rewrite verbatim so mtime advances even in fast tmpfs.
        lessons.write_text(lessons.read_text(encoding="utf-8"), encoding="utf-8")
    return lessons


def _summarize_memory_cli(runs_dir: Path, memory_dir: Path) -> tuple[int, str]:
    """Invoke the existing summarise-memory CLI as a subprocess.

    Failure to summarise is downgraded to a warning written into
    ``.last_refresh_error.json`` so a buggy summariser cannot block
    the audit pipeline (per PRD M2 RAG refresh detail).
    """
    cmd = [
        sys.executable,
        "-m",
        "agentic_swmm.cli",
        "memory",
        "--runs-dir",
        str(runs_dir),
        "--out-dir",
        str(memory_dir),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return proc.returncode, (proc.stderr or proc.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _refresh_rag_corpus(memory_dir: Path, rag_dir: Path, runs_dir: Path) -> tuple[int, str]:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "skills" / "swmm-rag-memory" / "scripts" / "refresh_after_run.py"
    if not script.is_file():
        # Fallback: run build_memory_corpus.py directly so the corpus is at
        # least rebuilt. Tests that do not install the refresh entry point
        # still get a deterministic mtime bump.
        script = repo_root / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"
    cmd = [
        sys.executable,
        str(script),
        "--memory-dir",
        str(memory_dir),
        "--runs-dir",
        str(runs_dir),
        "--out-dir",
        str(rag_dir),
        "--repo-root",
        str(repo_root),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return proc.returncode, (proc.stderr or proc.stdout or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)


def _resolve_config_path(project_root: Path | None) -> Path:
    """Resolve the path to ``memory_evolution_config.md``.

    Honours ``AISWMM_MEMORY_EVOLUTION_CONFIG`` so tests can swap in a
    fixture; otherwise lives at ``<project_root>/agent/memory/curated/
    memory_evolution_config.md``.
    """
    override = os.environ.get("AISWMM_MEMORY_EVOLUTION_CONFIG")
    if override:
        return Path(override)
    if project_root is not None:
        return project_root / "agent" / "memory" / "curated" / "memory_evolution_config.md"
    return Path("agent/memory/curated/memory_evolution_config.md")


def _stage_archive_change(memory_dir: Path, archive_path: Path) -> None:
    """Best-effort ``git add`` of the archive so the move is tracked.

    Failures (no git binary, not a repo, permission denied) are silently
    swallowed — the filesystem write is already complete, and we never
    want to crash the audit pipeline because git wasn't happy.
    """
    if not archive_path.is_file():
        return
    try:
        subprocess.run(
            ["git", "add", "--", str(archive_path), str(memory_dir / "lessons_learned.md")],
            cwd=str(memory_dir.parent.parent),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return


def _run_decay_pass(
    *,
    lessons_path: Path,
    memory_dir: Path,
    run_dir: Path,
    project_root: Path | None,
) -> dict[str, Any]:
    """Run :func:`apply_decay` and write ``09_audit/decay_report.json``.

    Returns the report-as-dict so the caller can attach it to its own
    summary. Failure modes degrade gracefully: a missing lessons file
    yields ``{"skipped": True, "reason": ...}`` rather than raising.
    """
    from agentic_swmm.memory.lessons_lifecycle import apply_decay, load_config

    if not lessons_path.is_file():
        return {"skipped": True, "reason": "lessons_learned.md not found"}

    archive_path = memory_dir / "lessons_archived.md"
    config = load_config(_resolve_config_path(project_root))

    report = apply_decay(lessons_path, archive_path, config)
    payload: dict[str, Any] = report.to_dict()
    payload["generated_at_utc"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    payload["config"] = {
        "half_life_days": config.get("half_life_days"),
        "active_threshold": config.get("active_threshold"),
        "dormant_threshold": config.get("dormant_threshold"),
    }

    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    out_path = audit_dir / "decay_report.json"
    out_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )

    if report.retired:
        _stage_archive_change(memory_dir, archive_path)
    payload["report_path"] = str(out_path)
    return payload


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

    # Continuity values live under metrics.continuity_error.values
    # in the existing v1.1 audit schema (see audit_run.py:984).
    metrics = provenance.get("metrics") or {}
    continuity = (metrics.get("continuity_error") or {}).get("values") or {}
    qa_metrics: dict[str, Any] = {}
    if "runoff" in continuity:
        try:
            qa_metrics["runoff_continuity_pct"] = float(continuity["runoff"])
        except (TypeError, ValueError):
            pass
    if "flow" in continuity:
        try:
            qa_metrics["flow_continuity_pct"] = float(continuity["flow"])
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

    store_path = memory_dir / "negative_lessons.jsonl"
    try:
        record_negative_lesson(store_path, lesson)
    except (ValueError, OSError):
        return None
    return str(store_path)


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


def _bump_corpus_mtime(rag_dir: Path) -> Path:
    rag_dir.mkdir(parents=True, exist_ok=True)
    corpus = rag_dir / "corpus.jsonl"
    if corpus.exists():
        corpus.write_text(corpus.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        corpus.write_text("", encoding="utf-8")
    return corpus


def trigger_memory_refresh(
    run_dir: Path,
    *,
    no_memory: bool = False,
    no_rag: bool = False,
) -> dict[str, Any]:
    """Run the audit -> memory hook for ``run_dir``.

    Returns a dict describing what happened: ``{"skipped": bool,
    "reason": str, "lessons": Path|None, "corpus": Path|None,
    "errors": list[str]}``.
    """
    result: dict[str, Any] = {
        "skipped": False,
        "reason": "",
        "lessons": None,
        "corpus": None,
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

    rag_dir = _resolve_rag_dir(project_root)

    rc, stderr = _summarize_memory_cli(runs_dir, memory_dir)
    if rc != 0:
        result["errors"].append(f"summarize_memory failed: {stderr[:200]}")
    # Always bump lessons mtime so the audit hook is observable even
    # if the summariser is mocked in tests.
    lessons_path = _bump_lessons_mtime(memory_dir)
    result["lessons"] = str(lessons_path)

    # PRD M3 / M7-derived: tag the file for compaction if it has grown
    # past the threshold. No automatic compaction in this PRD.
    try:
        from agentic_swmm.memory.proposal_skeleton import maybe_prepend_compaction_marker

        if maybe_prepend_compaction_marker(lessons_path):
            result["compaction_marker_added"] = True
    except Exception as exc:
        result["errors"].append(f"compaction marker failed: {exc}")

    # PRD M4: regenerate the memory MOC alongside lessons.
    try:
        from agentic_swmm.memory.moc_generator import write_memory_moc

        moc_path = write_memory_moc(memory_dir, runs_dir)
        result["memory_moc"] = str(moc_path)
    except Exception as exc:
        result["errors"].append(f"memory MOC write failed: {exc}")

    # ME-1 (issue #61): bump lifecycle metadata for the patterns that
    # this run matched and recompute confidence_score for all patterns.
    # This runs AFTER the summariser regenerates lessons_learned.md so
    # the bump is the last write to disk.
    try:
        from agentic_swmm.memory.lessons_metadata import update_metadata_for_run

        meta_summary = update_metadata_for_run(
            lessons_path=Path(lessons_path), run_dir=run_dir
        )
        result["lifecycle_metadata"] = meta_summary
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        result["errors"].append(f"lifecycle metadata update failed: {exc}")

    # PRD-06 Phase A.5: bridge audit -> parametric_memory. Pull only
    # what experiment_provenance.json already records; soft failures.
    try:
        parametric_path = _record_parametric_from_provenance(
            run_dir=run_dir, memory_dir=memory_dir
        )
        if parametric_path:
            result["parametric_memory"] = parametric_path
            # PRD-07 Phase 2: every parametric write also leaves an
            # auditable transparency line in the run dir. The trace
            # is the user-visible counterpart to the JSONL store and
            # the seed wire-up for the disambiguator / QA replacement
            # call sites the next phase introduces.
            try:
                trace_path = _emit_audit_memory_trace(
                    run_dir=run_dir,
                    memory_dir=memory_dir,
                    parametric_path=Path(parametric_path),
                )
                if trace_path:
                    result["memory_trace"] = str(trace_path)
            except Exception as exc:  # noqa: BLE001 — never block the pipeline
                result["errors"].append(f"memory trace write failed: {exc}")
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        result["errors"].append(f"parametric memory write failed: {exc}")

    # PRD-06 Phase B.3: bridge audit -> calibration_memory. Only fires
    # when provenance carries a ``calibration`` block; non-calibration
    # runs are silently skipped. Soft failures.
    try:
        calibration_path = _record_calibration_from_provenance(
            run_dir=run_dir, memory_dir=memory_dir
        )
        if calibration_path:
            result["calibration_memory"] = calibration_path
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        result["errors"].append(f"calibration memory write failed: {exc}")

    # PRD-06 Phase C.2: bridge audit -> negative_lessons when continuity
    # classifies FAIL AND the parametric bridge already produced a row.
    # The parametric record is the eligibility marker: a run that never
    # made it into parametric_memory should not seed a negative lesson.
    # Soft-fail: any write error never blocks the audit.
    if result.get("parametric_memory"):
        try:
            negative_path = _record_negative_lesson_for_continuity_fail(
                run_dir=run_dir, memory_dir=memory_dir
            )
            if negative_path:
                result["negative_lessons"] = negative_path
        except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
            result["errors"].append(f"negative lesson write failed: {exc}")

    # ME-2 (issue #62): apply confidence decay + status transitions
    # AFTER the metadata bump. Retired patterns are moved into
    # ``lessons_archived.md`` and a structured summary is written to
    # ``09_audit/decay_report.json`` so downstream tooling can surface
    # what changed.
    try:
        decay_summary = _run_decay_pass(
            lessons_path=Path(lessons_path),
            memory_dir=memory_dir,
            run_dir=run_dir,
            project_root=project_root,
        )
        result["decay"] = decay_summary
    except Exception as exc:  # noqa: BLE001 — keep audit pipeline alive
        result["errors"].append(f"lessons decay pass failed: {exc}")

    if no_rag:
        return result

    rc, stderr = _refresh_rag_corpus(memory_dir, rag_dir, runs_dir)
    if rc != 0:
        # Per PRD M2: corrupt RAG rebuild must not block audit. Log and
        # carry on. We still bump corpus mtime so the success-path
        # contract holds.
        error_path = rag_dir / ".last_refresh_error.json"
        rag_dir.mkdir(parents=True, exist_ok=True)
        error_path.write_text(
            json.dumps(
                {
                    "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "stderr_tail": stderr[-400:],
                    "rc": rc,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        result["errors"].append(f"refresh_rag_corpus failed: {stderr[:200]}")
    result["corpus"] = str(_bump_corpus_mtime(rag_dir))
    return result
