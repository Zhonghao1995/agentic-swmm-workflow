"""Tests for ``agentic_swmm.memory.run_progress`` (PRD-06 Phase C.4).

The long-run progress primitive exposes three verbs:

- :func:`write_checkpoint` — atomic write to ``progress.json``
- :func:`read_checkpoint` — ``None`` on missing/malformed
- :func:`summarize_progress` — one-line human summary
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.run_progress import (
    SCHEMA_VERSION,
    ProgressCheckpoint,
    read_checkpoint,
    summarize_progress,
    write_checkpoint,
)


def _make_ckpt(**over) -> ProgressCheckpoint:
    defaults = dict(
        run_id="run-1",
        algorithm="DREAM-ZS",
        iter_index=100,
        total_iters=1000,
        best_objective_so_far=0.72,
        wall_time_s=300.0,
        last_param_set={"manning_n": 0.013, "imdmax": 0.25},
        created_at="2026-05-19T00:00:00Z",
    )
    defaults.update(over)
    return ProgressCheckpoint(**defaults)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.0")


class WriteReadRoundTripTests(unittest.TestCase):
    def test_write_then_read_returns_same_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_checkpoint(run_dir, _make_ckpt())
            ckpt = read_checkpoint(run_dir)
        self.assertIsNotNone(ckpt)
        self.assertEqual(ckpt.run_id, "run-1")
        self.assertEqual(ckpt.algorithm, "DREAM-ZS")
        self.assertEqual(ckpt.iter_index, 100)
        self.assertEqual(ckpt.total_iters, 1000)
        self.assertAlmostEqual(ckpt.best_objective_so_far, 0.72, places=5)
        self.assertAlmostEqual(ckpt.wall_time_s, 300.0, places=3)
        self.assertEqual(ckpt.last_param_set["manning_n"], 0.013)

    def test_overwrites_existing_checkpoint(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_checkpoint(run_dir, _make_ckpt(iter_index=100))
            write_checkpoint(run_dir, _make_ckpt(iter_index=200))
            ckpt = read_checkpoint(run_dir)
        self.assertEqual(ckpt.iter_index, 200)

    def test_creates_run_dir_if_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "fresh" / "nested"
            write_checkpoint(run_dir, _make_ckpt())
            self.assertTrue((run_dir / "progress.json").is_file())

    def test_writes_schema_versioned_json(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_checkpoint(run_dir, _make_ckpt())
            payload = json.loads((run_dir / "progress.json").read_text("utf-8"))
        self.assertEqual(payload["schema_version"], "1.0")

    def test_auto_fills_created_at(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            ckpt = ProgressCheckpoint(
                run_id="r1",
                algorithm="SCE-UA",
                iter_index=0,
                total_iters=100,
                best_objective_so_far=0.0,
                wall_time_s=0.0,
                last_param_set={},
            )
            write_checkpoint(run_dir, ckpt)
            payload = json.loads((run_dir / "progress.json").read_text("utf-8"))
        self.assertTrue(payload["created_at"])
        self.assertTrue(payload["created_at"].endswith("Z"))


class ReadCheckpointFailureModes(unittest.TestCase):
    def test_missing_file_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertIsNone(read_checkpoint(Path(tmp)))

    def test_malformed_json_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "progress.json").write_text(
                "{not really json", encoding="utf-8"
            )
            self.assertIsNone(read_checkpoint(run_dir))

    def test_missing_required_field_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "progress.json").write_text(
                json.dumps({"run_id": "r1"}), encoding="utf-8"
            )
            self.assertIsNone(read_checkpoint(run_dir))

    def test_non_dict_payload_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "progress.json").write_text(
                json.dumps([1, 2, 3]), encoding="utf-8"
            )
            self.assertIsNone(read_checkpoint(run_dir))


class WriteValidationTests(unittest.TestCase):
    def test_empty_run_id_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_checkpoint(
                    Path(tmp),
                    _make_ckpt(run_id=""),
                )

    def test_empty_algorithm_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_checkpoint(
                    Path(tmp),
                    _make_ckpt(algorithm=""),
                )

    def test_negative_iter_index_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_checkpoint(
                    Path(tmp),
                    _make_ckpt(iter_index=-1),
                )

    def test_negative_total_iters_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_checkpoint(
                    Path(tmp),
                    _make_ckpt(total_iters=-1),
                )


class SummarizeProgressTests(unittest.TestCase):
    def test_renders_iter_and_objective(self) -> None:
        s = summarize_progress(
            _make_ckpt(
                algorithm="DREAM-ZS",
                iter_index=3200,
                total_iters=5000,
                best_objective_so_far=0.712,
                wall_time_s=2520.0,  # 42 min
            )
        )
        self.assertIn("DREAM-ZS", s)
        self.assertIn("iter 3200/5000", s)
        self.assertIn("0.712", s)
        self.assertIn("42 min", s)

    def test_renders_seconds_under_minute(self) -> None:
        s = summarize_progress(_make_ckpt(wall_time_s=30.0))
        self.assertIn("30.0s", s)

    def test_renders_hours_over_hour(self) -> None:
        s = summarize_progress(_make_ckpt(wall_time_s=7200.0))
        self.assertIn("2.0 h", s)


class AtomicWriteTests(unittest.TestCase):
    def test_no_tmp_file_lingers_after_success(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            write_checkpoint(run_dir, _make_ckpt())
            tmp_files = list(run_dir.glob("progress.json.tmp"))
        self.assertEqual(tmp_files, [])


if __name__ == "__main__":
    unittest.main()
