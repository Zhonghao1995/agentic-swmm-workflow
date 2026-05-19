"""Tests for ``agentic_swmm.agent.swmm_runtime.calibration_runner`` (PRD-06 C.5).

The facade exposes:

- :func:`run_calibration_with_checkpoints` — iterate loop with on-disk
  ProgressCheckpoint writes
- :func:`resume_from_checkpoint` — thin reader wrapper
- :func:`replay_iterations` — test convenience for deterministic
  trajectories
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.calibration_runner import (
    CalibrationIterationOutcome,
    CalibrationRunConfig,
    CalibrationResult,
    replay_iterations,
    resume_from_checkpoint,
    run_calibration_with_checkpoints,
)
from agentic_swmm.memory.run_progress import read_checkpoint


def _cfg(**overrides) -> CalibrationRunConfig:
    base = dict(
        run_id="r1",
        algorithm="sceua",
        total_iters=10,
        base_inp=Path("/tmp/model.inp"),
        observed_csv=Path("/tmp/obs.csv"),
        parameters=[("manning_n", 0.01, 0.03), ("imdmax", 0.1, 0.4)],
        objective="nse",
        checkpoint_every=1,
    )
    base.update(overrides)
    return CalibrationRunConfig(**base)


class CheckpointEveryIterationTests(unittest.TestCase):
    def test_every_iter_default(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.012 + i * 0.001, "imdmax": 0.2},
                    objective_value=0.5 + i * 0.05,
                )
                for i in range(10)
            ]
            result = run_calibration_with_checkpoints(
                _cfg(total_iters=10, checkpoint_every=1),
                run_dir,
                iterate_fn=replay_iterations(outcomes),
            )
            ckpt = read_checkpoint(run_dir)
            self.assertIsNotNone(ckpt)
            self.assertEqual(ckpt.iter_index, 10)
            self.assertEqual(ckpt.total_iters, 10)
        self.assertEqual(result.iterations_completed, 10)
        self.assertAlmostEqual(result.best_objective, 0.95, places=5)
        self.assertEqual(result.best_parameters["manning_n"], 0.012 + 9 * 0.001)

    def test_checkpoint_every_5_writes_at_5_and_10(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            recorded_iters: list[int] = []

            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.25},
                    objective_value=0.5 + i * 0.01,
                )
                for i in range(10)
            ]
            run_calibration_with_checkpoints(
                _cfg(total_iters=10, checkpoint_every=5),
                run_dir,
                iterate_fn=replay_iterations(outcomes),
                progress_callback=lambda c: recorded_iters.append(c.iter_index),
            )
            ckpt = read_checkpoint(run_dir)
            self.assertEqual(ckpt.iter_index, 10)

        # Only iters 5 and 10 should have triggered a checkpoint.
        self.assertEqual(recorded_iters, [5, 10])

    def test_checkpoint_every_5_no_write_before_iter_5(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            recorded: list[int] = []

            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.2},
                    objective_value=0.4,
                )
                for _ in range(4)
            ]
            cfg = _cfg(total_iters=4, checkpoint_every=5)
            run_calibration_with_checkpoints(
                cfg,
                run_dir,
                iterate_fn=replay_iterations(outcomes),
                progress_callback=lambda c: recorded.append(c.iter_index),
            )

        # checkpoint_every=5 but total=4 means: no periodic write, only
        # the final-iteration safety write fires (iter 4).
        self.assertEqual(recorded, [4])


class CrashSimulationTests(unittest.TestCase):
    def test_iterate_fn_raises_leaves_last_ckpt_readable(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            def raising(iter_idx: int, cfg: CalibrationRunConfig) -> CalibrationIterationOutcome:
                if iter_idx == 7:
                    raise RuntimeError("synthetic crash at iter 7")
                return CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.25},
                    objective_value=0.5 + iter_idx * 0.05,
                )

            result = run_calibration_with_checkpoints(
                _cfg(total_iters=10, checkpoint_every=1),
                run_dir,
                iterate_fn=raising,
            )
            ckpt = read_checkpoint(run_dir)
            self.assertIsNotNone(ckpt)
            self.assertEqual(ckpt.iter_index, 6)

        self.assertEqual(result.iterations_completed, 6)
        self.assertTrue(result.errors)


class ResumeHelperTests(unittest.TestCase):
    def test_resume_returns_checkpoint_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.25},
                    objective_value=0.6,
                )
            ]
            run_calibration_with_checkpoints(
                _cfg(total_iters=1, checkpoint_every=1),
                run_dir,
                iterate_fn=replay_iterations(outcomes),
            )
            ckpt = resume_from_checkpoint(run_dir)
        self.assertIsNotNone(ckpt)
        self.assertEqual(ckpt.iter_index, 1)

    def test_resume_returns_none_when_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(resume_from_checkpoint(Path(tmp)))


class WallTimeTests(unittest.TestCase):
    def test_wall_time_increases_monotonically(self) -> None:
        """wall_time_s in successive checkpoints uses monotonic()."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            elapsed_times: list[float] = []

            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.25},
                    objective_value=0.5,
                )
                for _ in range(3)
            ]
            run_calibration_with_checkpoints(
                _cfg(total_iters=3, checkpoint_every=1),
                run_dir,
                iterate_fn=replay_iterations(outcomes),
                progress_callback=lambda c: elapsed_times.append(c.wall_time_s),
            )

        self.assertEqual(len(elapsed_times), 3)
        self.assertGreaterEqual(elapsed_times[1], elapsed_times[0])
        self.assertGreaterEqual(elapsed_times[2], elapsed_times[1])
        self.assertGreaterEqual(elapsed_times[0], 0.0)


class ObjectiveDirectionTests(unittest.TestCase):
    def test_rmse_min_objective_finds_lowest(self) -> None:
        """For RMSE, smaller is better."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.011, "imdmax": 0.2},
                    objective_value=0.5,
                ),
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.2},
                    objective_value=0.3,
                ),
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.015, "imdmax": 0.2},
                    objective_value=0.4,
                ),
            ]
            result = run_calibration_with_checkpoints(
                _cfg(total_iters=3, objective="rmse"),
                run_dir,
                iterate_fn=replay_iterations(outcomes),
            )
        self.assertAlmostEqual(result.best_objective, 0.3, places=5)
        self.assertEqual(result.best_parameters["manning_n"], 0.013)

    def test_nse_max_objective_finds_highest(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            outcomes = [
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.011, "imdmax": 0.2},
                    objective_value=0.55,
                ),
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.013, "imdmax": 0.2},
                    objective_value=0.78,
                ),
                CalibrationIterationOutcome(
                    parameters={"manning_n": 0.015, "imdmax": 0.2},
                    objective_value=0.62,
                ),
            ]
            result = run_calibration_with_checkpoints(
                _cfg(total_iters=3, objective="nse"),
                run_dir,
                iterate_fn=replay_iterations(outcomes),
            )
        self.assertAlmostEqual(result.best_objective, 0.78, places=5)
        self.assertEqual(result.best_parameters["manning_n"], 0.013)


class ConfigValidationTests(unittest.TestCase):
    def test_negative_total_iters_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_calibration_with_checkpoints(
                    _cfg(total_iters=-1),
                    Path(tmp),
                )

    def test_zero_checkpoint_every_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_calibration_with_checkpoints(
                    _cfg(checkpoint_every=0),
                    Path(tmp),
                )

    def test_zero_total_iters_no_checkpoint_written(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = run_calibration_with_checkpoints(
                _cfg(total_iters=0),
                run_dir,
            )
        self.assertEqual(result.iterations_completed, 0)
        self.assertIsNone(read_checkpoint(run_dir))


class DefaultIterateFnTests(unittest.TestCase):
    def test_default_stub_produces_drifting_trajectory(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            result = run_calibration_with_checkpoints(
                _cfg(total_iters=5),
                run_dir,
            )
        # Default stub: 0.5 + i*0.01. After 5 iters best = 0.5 + 5*0.01.
        self.assertAlmostEqual(result.best_objective, 0.55, places=5)
        self.assertEqual(result.iterations_completed, 5)


class ReplayIterationsTests(unittest.TestCase):
    def test_raises_when_iter_exceeds_sequence(self) -> None:
        outcomes = [
            CalibrationIterationOutcome(
                parameters={"manning_n": 0.013},
                objective_value=0.5,
            )
        ]
        fn = replay_iterations(outcomes)
        self.assertEqual(fn(1, _cfg()).objective_value, 0.5)
        with self.assertRaises(IndexError):
            fn(2, _cfg())


if __name__ == "__main__":
    unittest.main()
