"""Thin calibration runner facade with checkpoint wiring (PRD-06 Phase C.5).

Why this module
---------------
``agentic_swmm.memory.run_progress`` shipped the on-disk primitive in
Round 3 but no production loop wrote through it. The real SCE-UA /
DREAM-ZS scripts live under ``skills/swmm-calibration/scripts/`` and
take many minutes per evaluation — wrapping them directly would force
every test to spawn SWMM.

This module is the *thin facade* the agent reaches for instead. It
exposes a single verb, :func:`run_calibration_with_checkpoints`, that

* iterates through a user-supplied number of evaluations,
* every ``checkpoint_every`` iterations writes a
  :class:`~agentic_swmm.memory.run_progress.ProgressCheckpoint`,
* honours a pluggable ``iterate_fn`` so tests can drive deterministic
  trajectories and real callers can delegate to spotpy / SCE-UA / etc.

A separate :func:`resume_from_checkpoint` helper lets long-running
loops attach to a partial run on restart.

Out of scope
------------
* The actual spotpy / SCE-UA wiring. The PRD draws a clean line: the
  facade is the *contract*, the skill scripts are the *implementation*.
  When the agent gains a "calibrate this case" surface it will pass an
  ``iterate_fn`` that delegates to the existing skill script — no
  heavy refactor of those scripts is required.
* Multi-chain coordination. ``ProgressCheckpoint`` carries one
  ``last_param_set`` so this facade emits one per iteration. Chain
  bookkeeping is the calibrator's own concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from time import monotonic
from typing import Callable, Iterable

from agentic_swmm.memory.run_progress import (
    ProgressCheckpoint,
    read_checkpoint,
    write_checkpoint,
)


# ---------------------------------------------------------------------------
# Config + result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationRunConfig:
    """Inputs for a checkpoint-aware calibration run.

    The shape is intentionally narrow: the facade only needs enough to
    drive a loop and emit checkpoints. The real spotpy setup_class
    machinery owns the deeper schema; the facade routes around it so
    tests do not need spotpy on the import path.
    """

    run_id: str
    algorithm: str
    total_iters: int
    base_inp: Path
    observed_csv: Path
    parameters: list[tuple[str, float, float]]
    objective: str = "nse"
    checkpoint_every: int = 1


@dataclass(frozen=True)
class CalibrationIterationOutcome:
    """One iteration's output: parameter set + objective value.

    ``parameters`` mirrors :attr:`ProgressCheckpoint.last_param_set`
    so the facade can hand it straight through.
    """

    parameters: dict[str, float]
    objective_value: float


@dataclass(frozen=True)
class CalibrationResult:
    """Outcome of a checkpoint-driven calibration run.

    The facade reports the bookkeeping the caller can act on: how many
    iterations actually completed (may be less than ``total_iters`` on
    crash), the final best objective, the parameter set that produced
    it, and the wall time the writer recorded for the last checkpoint.
    """

    run_id: str
    algorithm: str
    iterations_completed: int
    total_iters: int
    best_objective: float
    best_parameters: dict[str, float]
    wall_time_s: float
    final_checkpoint: ProgressCheckpoint | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core verbs
# ---------------------------------------------------------------------------


IterateFn = Callable[[int, CalibrationRunConfig], CalibrationIterationOutcome]


def _default_iterate(iter_idx: int, cfg: CalibrationRunConfig) -> CalibrationIterationOutcome:
    """Stub iterator used when the caller does not pass one.

    Returns a deterministic walk so callers get a fully-formed result
    even without spotpy installed. The objective value drifts toward
    ``1.0`` so tests can assert "best so far" updates monotonically.
    """
    base = 0.5
    drift = min(0.49, iter_idx * 0.01)
    parameters = {name: low + (high - low) * 0.5 for name, low, high in cfg.parameters}
    return CalibrationIterationOutcome(
        parameters=parameters,
        objective_value=base + drift,
    )


def _better(candidate: float, current_best: float, objective: str) -> bool:
    """Return True when ``candidate`` is better than ``current_best``.

    For the canonical objectives we ship: NSE / KGE — higher is better;
    RMSE — lower is better. Unknown objectives default to higher-is-
    better because the calibration_memory schema documents primary
    objectives in that family.
    """
    if objective.lower() == "rmse":
        return candidate < current_best
    return candidate > current_best


def run_calibration_with_checkpoints(
    cfg: CalibrationRunConfig,
    run_dir: Path,
    *,
    iterate_fn: IterateFn | None = None,
    progress_callback: Callable[[ProgressCheckpoint], None] | None = None,
) -> CalibrationResult:
    """Drive a calibration loop and emit checkpoints every N iterations.

    ``iterate_fn`` accepts ``(iter_idx, cfg)`` and returns one
    :class:`CalibrationIterationOutcome`. The default stub walks a
    deterministic objective trajectory so test fixtures do not need
    spotpy.

    A checkpoint is written every ``cfg.checkpoint_every`` iterations,
    AND always on the final iteration so the last state of the loop is
    durable. Crashes inside ``iterate_fn`` are caught: the best-so-far
    checkpoint stays on disk and the error is reported in the result.
    """
    if cfg.total_iters < 0:
        raise ValueError("CalibrationRunConfig.total_iters must be >= 0")
    if cfg.checkpoint_every < 1:
        raise ValueError("CalibrationRunConfig.checkpoint_every must be >= 1")

    iterator = iterate_fn or _default_iterate
    run_dir = Path(run_dir)

    # Initialise "best so far" sentinels. RMSE is min; NSE/KGE are max.
    is_min = cfg.objective.lower() == "rmse"
    best_obj = float("inf") if is_min else float("-inf")
    best_params: dict[str, float] = {}

    start = monotonic()
    completed = 0
    errors: list[str] = []
    last_ckpt: ProgressCheckpoint | None = None

    for iter_idx in range(1, cfg.total_iters + 1):
        try:
            outcome = iterator(iter_idx, cfg)
        except Exception as exc:  # noqa: BLE001 — surface but don't crash
            errors.append(f"iter {iter_idx}: {type(exc).__name__}: {exc}")
            break

        if _better(outcome.objective_value, best_obj, cfg.objective):
            best_obj = outcome.objective_value
            best_params = dict(outcome.parameters)

        completed = iter_idx
        is_final = iter_idx == cfg.total_iters
        is_period = iter_idx % cfg.checkpoint_every == 0
        if not (is_period or is_final):
            continue

        elapsed = monotonic() - start
        ckpt = ProgressCheckpoint(
            run_id=cfg.run_id,
            algorithm=cfg.algorithm,
            iter_index=iter_idx,
            total_iters=cfg.total_iters,
            best_objective_so_far=float(best_obj) if best_obj not in (
                float("inf"),
                float("-inf"),
            ) else 0.0,
            wall_time_s=elapsed,
            last_param_set=dict(outcome.parameters),
        )
        write_checkpoint(run_dir, ckpt)
        last_ckpt = ckpt
        if progress_callback is not None:
            try:
                progress_callback(ckpt)
            except Exception as exc:  # noqa: BLE001 — never break the loop
                errors.append(f"progress_callback iter {iter_idx}: {exc}")

    return CalibrationResult(
        run_id=cfg.run_id,
        algorithm=cfg.algorithm,
        iterations_completed=completed,
        total_iters=cfg.total_iters,
        best_objective=float(best_obj) if best_obj not in (
            float("inf"),
            float("-inf"),
        ) else 0.0,
        best_parameters=best_params,
        wall_time_s=monotonic() - start,
        final_checkpoint=last_ckpt,
        errors=errors,
    )


def resume_from_checkpoint(run_dir: Path) -> ProgressCheckpoint | None:
    """Return the last on-disk checkpoint for ``run_dir`` or ``None``.

    Thin wrapper around :func:`read_checkpoint` named for the caller-
    side intent so the facade reads like prose at the call site:
    ``ckpt = resume_from_checkpoint(run_dir) or new_start()``.
    """
    return read_checkpoint(run_dir)


def replay_iterations(
    outcomes: Iterable[CalibrationIterationOutcome],
) -> IterateFn:
    """Return an ``iterate_fn`` that replays a fixed sequence of outcomes.

    Test-side convenience for driving deterministic trajectories. The
    returned callable raises ``IndexError`` when the caller asks for
    one more iteration than the sequence provides — mirroring a
    "calibrator stopped early" path the result reports as an error.
    """
    materialised = list(outcomes)

    def _replay(iter_idx: int, cfg: CalibrationRunConfig) -> CalibrationIterationOutcome:
        if iter_idx < 1 or iter_idx > len(materialised):
            raise IndexError(
                f"replay_iterations only has {len(materialised)} outcomes;"
                f" iter {iter_idx} out of range"
            )
        return materialised[iter_idx - 1]

    return _replay
