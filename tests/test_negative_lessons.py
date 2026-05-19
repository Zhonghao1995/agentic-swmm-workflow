"""Tests for ``agentic_swmm.memory.negative_lessons`` (PRD-06 Phase C.2).

The negative-lessons store records parameter regions that consistently
fail. The deep module exposes four verbs:

- :func:`record_negative_lesson` — append one row, schema-validated
- :func:`recall_negative_lessons` — filter rows by exact-match field combo
- :func:`is_param_set_known_bad` — fuzzy lookup by parameter set
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.negative_lessons import (
    SCHEMA_VERSION,
    NegativeLesson,
    is_param_set_known_bad,
    recall_negative_lessons,
    record_negative_lesson,
)


def _make_lesson(**over) -> NegativeLesson:
    defaults = dict(
        run_id="run-default",
        case_name="case-default",
        lesson_type="continuity_fail",
        parameters_tried={"manning_n": 0.013, "imdmax": 0.25},
        metric_observed={"runoff_continuity_pct": 12.4},
        note="postflight FAIL",
        recorded_at="2026-05-19T00:00:00Z",
    )
    defaults.update(over)
    return NegativeLesson(**defaults)


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "1.0")


class RecordRoundTripTests(unittest.TestCase):
    def test_record_then_recall_returns_same_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(store, _make_lesson(run_id="r1"))
            lessons = recall_negative_lessons(store, {})
        self.assertEqual(len(lessons), 1)
        lesson = lessons[0]
        self.assertEqual(lesson.run_id, "r1")
        self.assertEqual(lesson.case_name, "case-default")
        self.assertEqual(lesson.lesson_type, "continuity_fail")
        self.assertEqual(lesson.parameters_tried["manning_n"], 0.013)
        self.assertEqual(lesson.metric_observed["runoff_continuity_pct"], 12.4)

    def test_record_auto_fills_recorded_at(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                NegativeLesson(
                    run_id="r1",
                    case_name="c1",
                    lesson_type="continuity_fail",
                    parameters_tried={"manning_n": 0.013},
                ),
            )
            rows = recall_negative_lessons(store, {})
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].recorded_at)
        self.assertTrue(rows[0].recorded_at.endswith("Z"))


class SchemaValidationTests(unittest.TestCase):
    def test_empty_run_id_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_negative_lesson(store, _make_lesson(run_id=""))
            self.assertIn("run_id", str(cm.exception))

    def test_empty_case_name_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_negative_lesson(store, _make_lesson(case_name=""))
            self.assertIn("case_name", str(cm.exception))

    def test_unknown_lesson_type_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            with self.assertRaises(ValueError) as cm:
                record_negative_lesson(store, _make_lesson(lesson_type="bogus"))
            self.assertIn("lesson_type", str(cm.exception))

    def test_all_enumerated_types_accepted(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            for lt in ("continuity_fail", "calibration_diverged", "non_physical_param"):
                record_negative_lesson(
                    store,
                    _make_lesson(run_id=f"r-{lt}", lesson_type=lt),
                )
            rows = recall_negative_lessons(store, {})
        self.assertEqual({r.lesson_type for r in rows}, {"continuity_fail", "calibration_diverged", "non_physical_param"})


class AppendOrderTests(unittest.TestCase):
    def test_torn_final_line_does_not_crash_reader(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(store, _make_lesson(run_id="ok-1"))
            record_negative_lesson(store, _make_lesson(run_id="ok-2"))
            with store.open("a", encoding="utf-8") as handle:
                handle.write('{"run_id": "torn", "case_name":')
            rows = recall_negative_lessons(store, {})
        self.assertEqual({r.run_id for r in rows}, {"ok-1", "ok-2"})


class RecallFilterTests(unittest.TestCase):
    def test_filter_by_case_name(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(store, _make_lesson(run_id="r1", case_name="saanich"))
            record_negative_lesson(store, _make_lesson(run_id="r2", case_name="tecnopolo"))
            saanich = recall_negative_lessons(store, {"case_name": "saanich"})
        self.assertEqual([r.run_id for r in saanich], ["r1"])

    def test_filter_by_lesson_type(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store, _make_lesson(run_id="r1", lesson_type="continuity_fail")
            )
            record_negative_lesson(
                store, _make_lesson(run_id="r2", lesson_type="calibration_diverged")
            )
            diverged = recall_negative_lessons(
                store, {"lesson_type": "calibration_diverged"}
            )
        self.assertEqual([r.run_id for r in diverged], ["r2"])

    def test_filter_by_nested_parameter_dotted_key(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(run_id="low", parameters_tried={"manning_n": 0.013}),
            )
            record_negative_lesson(
                store,
                _make_lesson(run_id="high", parameters_tried={"manning_n": 0.030}),
            )
            matches = recall_negative_lessons(
                store, {"parameters_tried.manning_n": 0.013}
            )
        self.assertEqual([r.run_id for r in matches], ["low"])

    def test_missing_file_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "missing.jsonl"
            self.assertEqual(recall_negative_lessons(store, {}), [])


class IsParamSetKnownBadTests(unittest.TestCase):
    def test_exact_match_returns_lesson(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(
                    run_id="rb",
                    case_name="saanich",
                    parameters_tried={"manning_n": 0.013, "imdmax": 0.25},
                ),
            )
            hit = is_param_set_known_bad(
                store, "saanich", {"manning_n": 0.013, "imdmax": 0.25}
            )
        self.assertIsNotNone(hit)
        self.assertEqual(hit.run_id, "rb")

    def test_within_tolerance_returns_lesson(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(
                    run_id="rb",
                    case_name="saanich",
                    parameters_tried={"manning_n": 0.020},
                ),
            )
            # 0.0204 is 2% off — within default 5% tolerance.
            hit = is_param_set_known_bad(store, "saanich", {"manning_n": 0.0204})
        self.assertIsNotNone(hit)

    def test_outside_tolerance_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(
                    run_id="rb",
                    case_name="saanich",
                    parameters_tried={"manning_n": 0.020},
                ),
            )
            # 0.030 is 50% off — outside default 5% tolerance.
            hit = is_param_set_known_bad(store, "saanich", {"manning_n": 0.030})
        self.assertIsNone(hit)

    def test_different_case_name_not_matched(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(case_name="saanich", parameters_tried={"manning_n": 0.013}),
            )
            hit = is_param_set_known_bad(store, "tecnopolo", {"manning_n": 0.013})
        self.assertIsNone(hit)

    def test_missing_key_in_candidate_skips_lesson(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(
                    case_name="saanich",
                    parameters_tried={"manning_n": 0.013, "imdmax": 0.25},
                ),
            )
            # Candidate set is missing "imdmax" — never flagged.
            hit = is_param_set_known_bad(store, "saanich", {"manning_n": 0.013})
        self.assertIsNone(hit)

    def test_tolerance_pct_zero_requires_exact(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(case_name="c1", parameters_tried={"manning_n": 0.013}),
            )
            self.assertIsNotNone(
                is_param_set_known_bad(
                    store, "c1", {"manning_n": 0.013}, tolerance_pct=0.0
                )
            )
            self.assertIsNone(
                is_param_set_known_bad(
                    store, "c1", {"manning_n": 0.0131}, tolerance_pct=0.0
                )
            )

    def test_negative_tolerance_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            with self.assertRaises(ValueError):
                is_param_set_known_bad(
                    store, "c1", {"manning_n": 0.013}, tolerance_pct=-1.0
                )

    def test_empty_store_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            self.assertIsNone(
                is_param_set_known_bad(store, "c1", {"manning_n": 0.013})
            )

    def test_zero_recorded_value_does_not_divide_by_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            record_negative_lesson(
                store,
                _make_lesson(
                    case_name="c1",
                    parameters_tried={"baseline_flow": 0.0},
                ),
            )
            # Tiny candidate close to zero — should be hit at default tol.
            hit = is_param_set_known_bad(
                store, "c1", {"baseline_flow": 0.0}
            )
        self.assertIsNotNone(hit)


if __name__ == "__main__":
    unittest.main()
