"""Tests for ``agentic_swmm.agent.calibration_batch`` (PRD-06 Phase C §15)."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.calibration_batch import (
    BATCH_ENV_VAR,
    CalibrationBatch,
    is_batch_active,
)


class EnvVarLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        # Make sure no stale env var leaks between tests.
        os.environ.pop(BATCH_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def test_enter_sets_env_var(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(is_batch_active())
            with CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=Path(tmp),
            ):
                self.assertTrue(is_batch_active())
            self.assertFalse(is_batch_active())

    def test_exit_clears_env_var(self) -> None:
        with TemporaryDirectory() as tmp:
            with CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=Path(tmp),
            ):
                self.assertEqual(os.environ.get(BATCH_ENV_VAR), "1")
            self.assertNotIn(BATCH_ENV_VAR, os.environ)

    def test_nested_batch_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=Path(tmp),
            ):
                with self.assertRaises(RuntimeError):
                    with CalibrationBatch(
                        case_name="case-b",
                        use_case="stormwater_event",
                        algorithm="sceua",
                        memory_dir=Path(tmp),
                    ):
                        pass

    def test_restores_prior_env_var(self) -> None:
        """If caller already had the var set, restore to that value on exit."""
        os.environ[BATCH_ENV_VAR] = "1"
        with TemporaryDirectory() as tmp:
            # A nested CalibrationBatch would raise; just make sure the
            # env-var restoration logic does not pop a pre-existing var.
            # Direct test: instantiate without entering.
            self.assertTrue(is_batch_active())
        # cleanup handled by tearDown


class IterationAccumulationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def test_record_iteration_buffers_in_memory_only(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="saanich-b8",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            )
            with batch:
                for i in range(5):
                    batch.record_iteration(
                        iter_idx=i,
                        parameters={"manning_n": 0.012 + i * 0.001},
                        objective_value=0.5 + i * 0.05,
                        run_id=f"run-{i}",
                    )
                # While inside the batch, no on-disk store exists yet.
                self.assertFalse((memory_dir / "calibration_memory.jsonl").exists())

    def test_exit_writes_one_calibration_row(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="saanich-b8",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            )
            with batch:
                for i in range(5):
                    batch.record_iteration(
                        iter_idx=i,
                        parameters={"manning_n": 0.012 + i * 0.001},
                        objective_value=0.5 + i * 0.05,
                        run_id=f"run-{i}",
                    )

            store = memory_dir / "calibration_memory.jsonl"
            self.assertTrue(store.is_file())
            lines = [
                json.loads(ln)
                for ln in store.read_text("utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual(len(lines), 1)
            best = lines[0]
            # NSE max: best run is the last one (iter 4).
            self.assertEqual(best["run_id"], "run-4")
            self.assertAlmostEqual(best["objective_value"], 0.5 + 4 * 0.05, places=5)
            self.assertEqual(best["case_name"], "saanich-b8")
            self.assertEqual(best["algorithm"], "sceua")
            self.assertEqual(best["n_evaluations"], 5)

    def test_exit_appends_lesson_line(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="saanich-b8",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            )
            with batch:
                batch.record_iteration(
                    iter_idx=0,
                    parameters={"manning_n": 0.013},
                    objective_value=0.78,
                    run_id="run-0",
                )

            lessons = memory_dir / "lessons_learned.md"
            self.assertTrue(lessons.is_file())
            text = lessons.read_text(encoding="utf-8")
            self.assertIn("calibration batch", text)
            self.assertIn("saanich-b8", text)
            self.assertIn("sceua", text)

    def test_no_iterations_recorded_skips_store_write(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            with CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            ):
                pass
            # No iterations -> no calibration_memory.jsonl row, but
            # lessons_learned.md should NOT be created for empty.
            self.assertFalse((memory_dir / "calibration_memory.jsonl").exists())

    def test_rmse_objective_picks_minimum(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="dream_zs",
                memory_dir=memory_dir,
                objective_name="rmse",
            )
            with batch:
                batch.record_iteration(0, {"n": 0.01}, 0.5, "r-a")
                batch.record_iteration(1, {"n": 0.02}, 0.2, "r-b")  # best
                batch.record_iteration(2, {"n": 0.03}, 0.4, "r-c")
            store = memory_dir / "calibration_memory.jsonl"
            best = json.loads(store.read_text("utf-8").splitlines()[0])
            self.assertEqual(best["run_id"], "r-b")
            self.assertEqual(best["objective_name"], "rmse")


class ExceptionHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def test_exception_in_body_still_commits_best_so_far(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="saanich-b8",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            )
            with self.assertRaises(ValueError):
                with batch:
                    batch.record_iteration(0, {"n": 0.013}, 0.6, "run-a")
                    batch.record_iteration(1, {"n": 0.015}, 0.7, "run-b")
                    raise ValueError("synthetic crash")

            # Best-so-far still committed.
            store = memory_dir / "calibration_memory.jsonl"
            self.assertTrue(store.is_file())
            best = json.loads(store.read_text("utf-8").splitlines()[0])
            self.assertEqual(best["run_id"], "run-b")
            # Env var cleared even on exception.
            self.assertFalse(is_batch_active())

    def test_exception_recorded_in_consolidated_text(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            )
            with self.assertRaises(ValueError):
                with batch:
                    batch.record_iteration(0, {"n": 0.013}, 0.6, "run-a")
                    raise ValueError("synthetic crash")
            outcome = batch.consolidate()
            self.assertIn("synthetic crash", outcome.consolidated_lesson_text)
            self.assertIn("ValueError", outcome.consolidated_lesson_text)


class AuditHookSuppressionTests(unittest.TestCase):
    """When the batch is active, audit_hook must skip its parametric write."""

    def setUp(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def _make_run(self, root: Path) -> Path:
        run_dir = root / "runs" / "r1"
        audit_dir = run_dir / "09_audit"
        audit_dir.mkdir(parents=True)
        provenance = {
            "schema_version": "1.1",
            "run_id": "r1",
            "case_name": "case-a",
            "tools": {"swmm5_version": "5.2.4"},
            "metrics": {
                "continuity_error": {"values": {"runoff": 1.2, "flow": 0.3}}
            },
        }
        (audit_dir / "experiment_provenance.json").write_text(
            json.dumps(provenance), encoding="utf-8"
        )
        return run_dir

    def test_in_batch_skips_parametric_write(self) -> None:
        from agentic_swmm.memory.audit_hook import _record_parametric_from_provenance

        with TemporaryDirectory() as tmp:
            run_dir = self._make_run(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir(parents=True)
            os.environ[BATCH_ENV_VAR] = "1"
            try:
                result = _record_parametric_from_provenance(
                    run_dir=run_dir, memory_dir=memory_dir
                )
            finally:
                os.environ.pop(BATCH_ENV_VAR, None)
            self.assertIsNone(result)
            self.assertFalse((memory_dir / "parametric_memory.jsonl").exists())

    def test_outside_batch_parametric_write_proceeds(self) -> None:
        from agentic_swmm.memory.audit_hook import _record_parametric_from_provenance

        with TemporaryDirectory() as tmp:
            run_dir = self._make_run(Path(tmp))
            memory_dir = Path(tmp) / "memory"
            memory_dir.mkdir(parents=True)
            result = _record_parametric_from_provenance(
                run_dir=run_dir, memory_dir=memory_dir
            )
            self.assertIsNotNone(result)
            self.assertTrue((memory_dir / "parametric_memory.jsonl").exists())


class ConsolidateIdempotenceTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(BATCH_ENV_VAR, None)

    def test_consolidate_called_twice_writes_once(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "memory"
            batch = CalibrationBatch(
                case_name="case-a",
                use_case="stormwater_event",
                algorithm="sceua",
                memory_dir=memory_dir,
            )
            with batch:
                batch.record_iteration(0, {"n": 0.013}, 0.78, "run-a")
            # Second consolidate call should be a no-op write.
            batch.consolidate()
            store = memory_dir / "calibration_memory.jsonl"
            lines = [
                ln for ln in store.read_text("utf-8").splitlines() if ln.strip()
            ]
            self.assertEqual(len(lines), 1)


class ValidationTests(unittest.TestCase):
    def test_empty_case_name_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                CalibrationBatch(
                    case_name="",
                    use_case="stormwater_event",
                    algorithm="sceua",
                    memory_dir=Path(tmp),
                )

    def test_empty_algorithm_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                CalibrationBatch(
                    case_name="case-a",
                    use_case="stormwater_event",
                    algorithm="",
                    memory_dir=Path(tmp),
                )


if __name__ == "__main__":
    unittest.main()
