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
    CALIBRATION_STATUS_VALUES,
    SCHEMA_VERSION,
    ParametricRecord,
    recall_parametric,
    record_parametric_run,
)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_constant(self) -> None:
        # Round 5: parametric_memory bumped 1.0 -> 2.0 (PRD-06 §4.1).
        self.assertEqual(SCHEMA_VERSION, "2.0")


def _write(store: Path, **overrides: Any) -> ParametricRecord:
    defaults: dict[str, Any] = dict(
        run_id="run-default",
        case_name="case-default",
        swmm_version="5.2.4",
        model_structure={"routing": "dynamic_wave"},
        qa_metrics={"runoff_continuity_pct": 0.5},
        performance_metrics={},
        watershed_classification={"size_km2": 1.0, "impervious_pct": 30.0},
        calibration_status=None,
        parameter_set_ref=None,
        evidence_runs_count=1,
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


# ---------------------------------------------------------------------------
# Round 5 / PRD-06 §4.1 — schema 2.0 fields, validation, and dotted recall.
# ---------------------------------------------------------------------------


class Schema2FieldsTests(unittest.TestCase):
    """ParametricRecord carries the 2.0 fields end-to-end."""

    def test_record_with_all_new_fields_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            record = ParametricRecord(
                run_id="r-full",
                case_name="saanich-b8",
                swmm_version="5.2.4",
                model_structure={"routing": "dynamic_wave"},
                qa_metrics={"runoff_continuity_pct": 0.18},
                performance_metrics={
                    "nse": 0.68,
                    "kge": 0.71,
                    "pbias_pct": -2.4,
                    "peak_flow_error_pct": 8.1,
                    "peak_timing_error_min": 15,
                },
                watershed_classification={
                    "size_km2": 12.4,
                    "impervious_pct": 38.0,
                    "climate": "temperate-marine",
                    "land_use_dominant": "suburban-residential",
                },
                calibration_status="calibrated_against_observed",
                parameter_set_ref="calibration_memory/run_42",
                evidence_runs_count=37,
                recorded_utc="2026-05-19T14:35:00Z",
            )
            record_parametric_run(store, record)
            rows = recall_parametric(store, {})
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["performance_metrics"]["nse"], 0.68)
        self.assertEqual(row["watershed_classification"]["land_use_dominant"], "suburban-residential")
        self.assertEqual(row["calibration_status"], "calibrated_against_observed")
        self.assertEqual(row["parameter_set_ref"], "calibration_memory/run_42")
        self.assertEqual(row["evidence_runs_count"], 37)
        self.assertEqual(row["schema_version"], "2.0")

    def test_legacy_fields_only_populates_safe_defaults(self) -> None:
        """A 1.0-style caller (no new fields) gets 2.0 defaults populated."""
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            record_parametric_run(
                store,
                ParametricRecord(run_id="r1", case_name="c1"),
            )
            rows = recall_parametric(store, {})
        row = rows[0]
        self.assertEqual(row["schema_version"], "2.0")
        self.assertEqual(row["evidence_runs_count"], 1)
        self.assertIsNone(row["calibration_status"])
        self.assertIsNone(row["parameter_set_ref"])
        self.assertEqual(row["watershed_classification"], {})
        self.assertEqual(row["performance_metrics"], {})


class Schema2ValidationTests(unittest.TestCase):
    """``record_parametric_run`` enforces the 2.0 invariants."""

    def test_calibration_status_values_set_matches_prd(self) -> None:
        self.assertEqual(
            set(CALIBRATION_STATUS_VALUES),
            {"uncalibrated", "calibrated_against_observed", "validation_only"},
        )

    def test_invalid_calibration_status_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id="r1",
                        case_name="c1",
                        calibration_status="not_a_real_status",
                    ),
                )
            self.assertIn("calibration_status", str(cm.exception))

    def test_each_valid_calibration_status_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            for status in CALIBRATION_STATUS_VALUES:
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id=f"r-{status}",
                        case_name="c1",
                        calibration_status=status,
                    ),
                )
        # All three writes succeeded — verified by the call returning.

    def test_calibration_status_none_accepted(self) -> None:
        # ``None`` means "no claim about calibration state" — the most
        # common shape for un-tuned runs from the audit hook.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            record_parametric_run(
                store,
                ParametricRecord(
                    run_id="r1", case_name="c1", calibration_status=None
                ),
            )

    def test_non_dict_watershed_classification_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id="r1",
                        case_name="c1",
                        watershed_classification=["not", "a", "dict"],  # type: ignore[arg-type]
                    ),
                )
            self.assertIn("watershed_classification", str(cm.exception))

    def test_non_dict_performance_metrics_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id="r1",
                        case_name="c1",
                        performance_metrics="kge=0.71",  # type: ignore[arg-type]
                    ),
                )
            self.assertIn("performance_metrics", str(cm.exception))

    def test_evidence_runs_count_must_be_positive_int(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError):
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id="r1", case_name="c1", evidence_runs_count=0
                    ),
                )
            with self.assertRaises(ValueError):
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id="r1",
                        case_name="c1",
                        evidence_runs_count=-3,
                    ),
                )

    def test_evidence_runs_count_rejects_bool(self) -> None:
        # ``bool`` is a subclass of ``int``; we forbid it to keep the
        # column type honest.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            with self.assertRaises(ValueError):
                record_parametric_run(
                    store,
                    ParametricRecord(
                        run_id="r1",
                        case_name="c1",
                        evidence_runs_count=True,  # type: ignore[arg-type]
                    ),
                )


class Schema2DottedRecallTests(unittest.TestCase):
    """Recall filters can reach into the new 2.0 nested blocks."""

    def test_filter_by_watershed_size_km2(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write(
                store,
                run_id="small",
                watershed_classification={"size_km2": 0.5, "impervious_pct": 30.0},
            )
            _write(
                store,
                run_id="medium",
                watershed_classification={"size_km2": 12.4, "impervious_pct": 38.0},
            )
            _write(
                store,
                run_id="large",
                watershed_classification={"size_km2": 50.0, "impervious_pct": 65.0},
            )
            hits = recall_parametric(
                store, {"watershed_classification.size_km2": 12.4}
            )
        self.assertEqual([r["run_id"] for r in hits], ["medium"])

    def test_filter_by_performance_nse(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write(store, run_id="poor", performance_metrics={"nse": 0.42})
            _write(store, run_id="good", performance_metrics={"nse": 0.72})
            hits = recall_parametric(
                store, {"performance_metrics.nse": 0.72}
            )
        self.assertEqual([r["run_id"] for r in hits], ["good"])

    def test_filter_by_calibration_status_scalar(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            _write(store, run_id="raw", calibration_status="uncalibrated")
            _write(
                store,
                run_id="tuned",
                calibration_status="calibrated_against_observed",
            )
            _write(store, run_id="val", calibration_status="validation_only")
            hits = recall_parametric(
                store, {"calibration_status": "calibrated_against_observed"}
            )
        self.assertEqual([r["run_id"] for r in hits], ["tuned"])


if __name__ == "__main__":
    unittest.main()
