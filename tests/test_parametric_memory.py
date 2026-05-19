"""Tests for ``agentic_swmm.memory.parametric_memory`` (PRD-06 Phase A.1).

The parametric memory layer records one JSONL line per SWMM run with
quantitative provenance (QA continuity, performance metrics, watershed
classification, model structure). The deep module exposes two verbs:

- :func:`record_parametric_run` — append one row, schema-validated
- :func:`recall_parametric` — filter rows by exact-match field combo
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.memory.parametric_memory import (
    SCHEMA_VERSION,
    ParametricRecord,
    recall_parametric,
    record_parametric_run,
)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.0")


def _write(store: Path, **overrides: Any) -> ParametricRecord:
    defaults = dict(
        run_id="run-default",
        case_name="case-default",
        swmm_version="5.2.4",
        model_structure={"routing": "dynamic_wave"},
        qa_metrics={"runoff_continuity_pct": 0.5},
        performance_metrics={},
        watershed_classification={"size_km2": 1.0, "impervious_pct": 30.0},
        recorded_utc="2026-05-19T00:00:00Z",
    )
    defaults.update(overrides)
    rec = ParametricRecord(**defaults)
    record_parametric_run(store, rec)
    return rec


class RecordRoundTripTests(unittest.TestCase):
    def test_record_then_recall_returns_same_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            record = ParametricRecord(
                run_id="20260519-143022_urbancase_run",
                case_name="saanich-b8",
                swmm_version="5.2.4",
                model_structure={"routing": "dynamic_wave", "infiltration": "horton"},
                qa_metrics={"runoff_continuity_pct": 0.18, "flow_continuity_pct": 0.04},
                performance_metrics={},
                watershed_classification={"size_km2": 12.4, "impervious_pct": 38.0},
                recorded_utc="2026-05-19T14:35:00Z",
            )
            record_parametric_run(store, record)
            rows = recall_parametric(store, {})

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["schema_version"], SCHEMA_VERSION)
        self.assertEqual(row["run_id"], "20260519-143022_urbancase_run")
        self.assertEqual(row["case_name"], "saanich-b8")
        self.assertEqual(row["swmm_version"], "5.2.4")
        self.assertEqual(row["qa_metrics"]["runoff_continuity_pct"], 0.18)


class SchemaValidationTests(unittest.TestCase):
    def test_empty_run_id_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_parametric_run(
                    store, ParametricRecord(run_id="", case_name="case")
                )
            self.assertIn("run_id", str(cm.exception))

    def test_empty_case_name_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_parametric_run(
                    store, ParametricRecord(run_id="r1", case_name="")
                )
            self.assertIn("case_name", str(cm.exception))


class AppendOrderTests(unittest.TestCase):
    def test_records_preserved_in_insertion_order(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            for i in range(5):
                _write(store, run_id=f"run-{i}", case_name="ordering-case")
            rows = recall_parametric(store, {})
        self.assertEqual([r["run_id"] for r in rows], [f"run-{i}" for i in range(5)])

    def test_torn_final_line_does_not_crash_reader(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write(store, run_id="ok-1", case_name="case-x")
            _write(store, run_id="ok-2", case_name="case-x")
            # Simulate a torn JSON line (concurrent writer mid-flush).
            with store.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "torn", "case_name":')
            rows = recall_parametric(store, {})
        self.assertEqual({r["run_id"] for r in rows}, {"ok-1", "ok-2"})


class RecallFilterTests(unittest.TestCase):
    def test_filter_by_case_name_returns_subset(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write(store, run_id="r1", case_name="saanich-b8")
            _write(store, run_id="r2", case_name="tecnopolo")
            _write(store, run_id="r3", case_name="saanich-b8")

            saanich = recall_parametric(store, {"case_name": "saanich-b8"})
            tecnopolo = recall_parametric(store, {"case_name": "tecnopolo"})

        self.assertEqual({r["run_id"] for r in saanich}, {"r1", "r3"})
        self.assertEqual({r["run_id"] for r in tecnopolo}, {"r2"})

    def test_filter_by_nested_dotted_key(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write(
                store,
                run_id="dyn",
                model_structure={"routing": "dynamic_wave"},
            )
            _write(
                store,
                run_id="kin",
                model_structure={"routing": "kinematic_wave"},
            )

            dyn = recall_parametric(
                store, {"model_structure.routing": "dynamic_wave"}
            )

        self.assertEqual([r["run_id"] for r in dyn], ["dyn"])

    def test_missing_file_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "does_not_exist.jsonl"
            self.assertEqual(recall_parametric(store, {}), [])


if __name__ == "__main__":
    unittest.main()
