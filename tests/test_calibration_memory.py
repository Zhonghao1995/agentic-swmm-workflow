"""Tests for ``agentic_swmm.memory.calibration_memory`` (PRD-06 Phase B.3).

The calibration memory layer records one JSONL line per accepted
calibration with quantitative provenance (algorithm, parameter set,
goodness-of-fit). The deep module exposes two verbs:

- :func:`record_calibration_run` — append one row, schema-validated
- :func:`recall_calibration` — filter rows by exact-match field combo
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.memory.calibration_memory import (
    SCHEMA_VERSION,
    CalibrationRecord,
    recall_calibration,
    record_calibration_run,
)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.0")


def _write(store: Path, **overrides: Any) -> CalibrationRecord:
    defaults = dict(
        run_id="run-default",
        case_name="case-default",
        use_case="stormwater_event",
        algorithm="sceua",
        parameters={"manning_n": 0.013},
        objective_name="NSE",
        objective_value=0.78,
        secondary_metrics={"pbias_pct": -3.2, "rmse": 0.043},
        swmm5_version="5.2.4",
        n_evaluations=200,
        wall_time_s=120.0,
        created_at="2026-05-19T00:00:00Z",
    )
    defaults.update(overrides)
    rec = CalibrationRecord(**defaults)
    record_calibration_run(store, rec)
    return rec


class RecordRoundTripTests(unittest.TestCase):
    def test_record_then_recall_returns_same_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            record = CalibrationRecord(
                run_id="20260519-143022_calib_run",
                case_name="saanich-b8",
                use_case="stormwater_event",
                algorithm="sceua",
                parameters={"manning_n": 0.013, "imdmax": 0.25},
                objective_name="NSE",
                objective_value=0.78,
                secondary_metrics={"pbias_pct": -3.2, "rmse": 0.043},
                swmm5_version="5.2.4",
                n_evaluations=200,
                wall_time_s=120.0,
                created_at="2026-05-19T14:35:00Z",
            )
            record_calibration_run(store, record)
            rows = recall_calibration(store, {})

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["schema_version"], SCHEMA_VERSION)
        self.assertEqual(row["run_id"], "20260519-143022_calib_run")
        self.assertEqual(row["case_name"], "saanich-b8")
        self.assertEqual(row["algorithm"], "sceua")
        self.assertEqual(row["objective_name"], "NSE")
        self.assertAlmostEqual(row["objective_value"], 0.78, places=6)
        self.assertEqual(row["parameters"]["manning_n"], 0.013)


class SchemaValidationTests(unittest.TestCase):
    def test_empty_run_id_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_calibration_run(
                    store, CalibrationRecord(run_id="", case_name="case")
                )
            self.assertIn("run_id", str(cm.exception))

    def test_empty_case_name_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_calibration_run(
                    store, CalibrationRecord(run_id="r1", case_name="")
                )
            self.assertIn("case_name", str(cm.exception))


class AppendOrderTests(unittest.TestCase):
    def test_records_preserved_in_insertion_order(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            for i in range(5):
                _write(store, run_id=f"run-{i}", case_name="ordering-case")
            rows = recall_calibration(store, {})
        self.assertEqual([r["run_id"] for r in rows], [f"run-{i}" for i in range(5)])

    def test_torn_final_line_does_not_crash_reader(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write(store, run_id="ok-1", case_name="case-x")
            _write(store, run_id="ok-2", case_name="case-x")
            # Simulate a torn JSON line (concurrent writer mid-flush).
            with store.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "torn", "case_name":')
            rows = recall_calibration(store, {})
        self.assertEqual({r["run_id"] for r in rows}, {"ok-1", "ok-2"})


class RecallFilterTests(unittest.TestCase):
    def test_filter_by_algorithm_returns_subset(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write(store, run_id="r1", case_name="saanich", algorithm="sceua")
            _write(store, run_id="r2", case_name="saanich", algorithm="dream_zs")
            _write(store, run_id="r3", case_name="saanich", algorithm="sceua")

            sceua = recall_calibration(store, {"algorithm": "sceua"})
            dream = recall_calibration(store, {"algorithm": "dream_zs"})

        self.assertEqual({r["run_id"] for r in sceua}, {"r1", "r3"})
        self.assertEqual({r["run_id"] for r in dream}, {"r2"})

    def test_filter_by_use_case_returns_subset(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write(store, run_id="r1", use_case="stormwater_event")
            _write(store, run_id="r2", use_case="lid_optimization")
            sw = recall_calibration(store, {"use_case": "stormwater_event"})
        self.assertEqual({r["run_id"] for r in sw}, {"r1"})

    def test_filter_by_nested_parameter_dotted_key(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write(store, run_id="low", parameters={"manning_n": 0.013})
            _write(store, run_id="high", parameters={"manning_n": 0.030})

            matches = recall_calibration(
                store, {"parameters.manning_n": 0.013}
            )
        self.assertEqual([r["run_id"] for r in matches], ["low"])

    def test_filter_by_nested_secondary_metric(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write(store, run_id="m1", secondary_metrics={"pbias_pct": -3.2})
            _write(store, run_id="m2", secondary_metrics={"pbias_pct": 5.0})

            matches = recall_calibration(
                store, {"secondary_metrics.pbias_pct": -3.2}
            )
        self.assertEqual([r["run_id"] for r in matches], ["m1"])

    def test_empty_filters_returns_all(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write(store, run_id="a")
            _write(store, run_id="b")
            self.assertEqual(len(recall_calibration(store, {})), 2)
            self.assertEqual(len(recall_calibration(store, None)), 2)

    def test_missing_file_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "does_not_exist.jsonl"
            self.assertEqual(recall_calibration(store, {}), [])


if __name__ == "__main__":
    unittest.main()
