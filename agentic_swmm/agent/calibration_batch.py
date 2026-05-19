"""``CalibrationBatch`` — suppress per-iteration memory writes (PRD-06 C §15).

A modeler iterating 50 calibrations is one *workflow*, not 50. Without
a batch wrapper the audit hook would write 50 rows into
``parametric_memory.jsonl`` and 50 lines into ``lessons_learned.md`` —
the user's memory store would be flooded by the inner sweep instead of
recording the outcome that matters: which parameter set won.

This module ships a context manager that flips a process-level env var
(:data:`BATCH_ENV_VAR`) on enter and clears it on exit. Other hooks
read the var and short-circuit. On exit we commit:

1. One :class:`CalibrationRecord` row (the best iteration) to
   ``calibration_memory.jsonl``.
2. One human-readable line to ``lessons_learned.md``.

Exception behaviour
-------------------
If the context body raises, the batch still commits the best-so-far
iteration (the partial work is useful) and the exception is recorded
in :attr:`CalibrationBatchOutcome.consolidated_lesson_text`. The
context returns ``False`` from ``__exit__`` so the exception
propagates — callers can decide whether to swallow.

Nested batches
--------------
Re-entering ``CalibrationBatch`` while one is already active raises
``RuntimeError`` — the env-var flag is single-flagged, and nesting
would silently lose lessons.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.memory.calibration_memory import (
    CalibrationRecord,
    record_calibration_run,
)


BATCH_ENV_VAR = "AISWMM_IN_CALIBRATION_BATCH"


@dataclass(frozen=True)
class CalibrationBatchOutcome:
    """Consolidated summary of a calibration batch.

    All fields are optional-friendly so a batch that recorded zero
    iterations (the user called ``__enter__`` but the loop never fired)
    still returns a sane object.
    """

    n_iterations: int
    best_objective_value: float | None
    best_objective_name: str
    best_parameters: dict[str, float]
    best_run_id: str | None
    consolidated_lesson_text: str
    started_at: str
    ended_at: str


@dataclass
class _Iteration:
    iter_idx: int
    parameters: dict[str, float]
    objective_value: float
    run_id: str


class CalibrationBatch:
    """Context manager that batches calibration iterations.

    Example::

        with CalibrationBatch(
            case_name="saanich-b8",
            use_case="stormwater_event",
            algorithm="sceua",
            memory_dir=memory_dir,
            objective_name="nse",
        ) as batch:
            for iter_idx, params, obj, run_id in loop:
                batch.record_iteration(iter_idx, params, obj, run_id)

    On ``__exit__`` (success or failure) the batch writes one
    consolidated record to ``calibration_memory.jsonl`` and appends one
    summary line to ``lessons_learned.md``. Per-iteration audit-hook
    writes that observe :data:`BATCH_ENV_VAR` skip themselves while the
    batch is active.
    """

    def __init__(
        self,
        *,
        case_name: str,
        use_case: str,
        algorithm: str,
        memory_dir: Path,
        objective_name: str = "nse",
        swmm5_version: str | None = None,
    ) -> None:
        if not case_name or not case_name.strip():
            raise ValueError("CalibrationBatch.case_name must be a non-empty string")
        if not algorithm or not algorithm.strip():
            raise ValueError("CalibrationBatch.algorithm must be a non-empty string")
        self._case_name = case_name
        self._use_case = use_case
        self._algorithm = algorithm
        self._memory_dir = Path(memory_dir)
        self._objective_name = objective_name
        self._swmm5_version = swmm5_version
        self._iterations: list[_Iteration] = []
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._committed = False
        self._prior_env: str | None = None
        self._cached_outcome: CalibrationBatchOutcome | None = None

    # ------------------------------------------------------------------ enter/exit
    def __enter__(self) -> "CalibrationBatch":
        if os.environ.get(BATCH_ENV_VAR, "").strip() in {"1", "true", "True", "yes"}:
            raise RuntimeError("nested CalibrationBatch not supported")
        self._prior_env = os.environ.get(BATCH_ENV_VAR)
        os.environ[BATCH_ENV_VAR] = "1"
        self._started_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        # Always run the consolidation; failure inside should never
        # silently lose work, but we keep the writes isolated.
        try:
            self.consolidate(exception=exc_val)
        finally:
            # Restore the env var to whatever the caller had before. The
            # default is "delete the key".
            if self._prior_env is None:
                os.environ.pop(BATCH_ENV_VAR, None)
            else:
                os.environ[BATCH_ENV_VAR] = self._prior_env
        # Returning False propagates any exception raised inside the body.
        return False

    # ------------------------------------------------------------------ recording
    def record_iteration(
        self,
        iter_idx: int,
        parameters: dict[str, float],
        objective_value: float,
        run_id: str,
    ) -> None:
        """Buffer one iteration in memory (no on-disk write yet)."""
        self._iterations.append(
            _Iteration(
                iter_idx=iter_idx,
                parameters=dict(parameters),
                objective_value=float(objective_value),
                run_id=run_id,
            )
        )

    # ------------------------------------------------------------------ consolidation
    def consolidate(
        self, *, exception: BaseException | None = None
    ) -> CalibrationBatchOutcome:
        """Write the consolidated record + lesson line, return the outcome.

        Idempotent: a second call returns the cached outcome without
        rewriting. Called automatically by ``__exit__`` but exposed so
        callers can flush the consolidated record explicitly.
        """
        if self._committed and self._cached_outcome is not None:
            return self._cached_outcome

        outcome = self._build_outcome(exception=exception, write_side_effects=True)
        self._committed = True
        self._cached_outcome = outcome
        return outcome

    # ------------------------------------------------------------------ internals
    def _select_best(self) -> _Iteration | None:
        if not self._iterations:
            return None
        if self._objective_name.lower() == "rmse":
            return min(self._iterations, key=lambda i: i.objective_value)
        return max(self._iterations, key=lambda i: i.objective_value)

    def _build_outcome(
        self,
        *,
        exception: BaseException | None,
        write_side_effects: bool,
    ) -> CalibrationBatchOutcome:
        self._ended_at = (
            datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        best = self._select_best()
        n = len(self._iterations)
        lines: list[str] = []
        lines.append(
            f"calibration batch | case={self._case_name} use_case={self._use_case} "
            f"algorithm={self._algorithm} iterations={n}"
        )
        if best is not None:
            params_str = ", ".join(
                f"{k}={v:.4g}" for k, v in sorted(best.parameters.items())
            )
            lines.append(
                f"  best {self._objective_name}={best.objective_value:.4g} "
                f"(run_id={best.run_id}; params={params_str})"
            )
        else:
            lines.append("  no iterations recorded")
        if exception is not None:
            lines.append(
                f"  exited with exception {type(exception).__name__}: {exception}"
            )
        consolidated_text = "\n".join(lines)

        if write_side_effects and best is not None:
            self._write_calibration_record(best, consolidated_text)
            self._append_lesson_line(consolidated_text)

        return CalibrationBatchOutcome(
            n_iterations=n,
            best_objective_value=best.objective_value if best is not None else None,
            best_objective_name=self._objective_name,
            best_parameters=dict(best.parameters) if best is not None else {},
            best_run_id=best.run_id if best is not None else None,
            consolidated_lesson_text=consolidated_text,
            started_at=self._started_at or "",
            ended_at=self._ended_at or "",
        )

    def _write_calibration_record(self, best: _Iteration, consolidated_text: str) -> None:
        store = self._memory_dir / "calibration_memory.jsonl"
        try:
            record = CalibrationRecord(
                run_id=best.run_id,
                case_name=self._case_name,
                use_case=self._use_case,
                algorithm=self._algorithm,
                parameters=best.parameters,
                objective_name=self._objective_name,
                objective_value=best.objective_value,
                secondary_metrics={},
                swmm5_version=self._swmm5_version,
                n_evaluations=len(self._iterations),
                wall_time_s=None,
                created_at=self._ended_at,
            )
            record_calibration_run(store, record)
        except (ValueError, OSError):
            # Soft-fail to match the calibration_memory bridge contract.
            return

    def _append_lesson_line(self, text: str) -> None:
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        lessons = self._memory_dir / "lessons_learned.md"
        prefix = "" if lessons.exists() else "<!-- schema_version: 1.1 -->\n# Lessons\n"
        existing = lessons.read_text(encoding="utf-8") if lessons.exists() else ""
        with lessons.open("w", encoding="utf-8") as handle:
            if prefix:
                handle.write(prefix)
            elif existing and not existing.endswith("\n"):
                handle.write(existing)
                handle.write("\n")
            else:
                handle.write(existing)
            handle.write(f"- {text.splitlines()[0]}\n")


def is_batch_active() -> bool:
    """Return True when a :class:`CalibrationBatch` is currently active.

    Audit hooks call this to decide whether to skip per-run writes.
    """
    return os.environ.get(BATCH_ENV_VAR, "").strip() in {"1", "true", "True", "yes"}
