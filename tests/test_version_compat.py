"""Tests for ``agentic_swmm.memory.version_compat`` (PRD-06 Phase C.3).

The migration registry covers three stores today:

- ``parametric_memory``
- ``calibration_memory``
- ``negative_lessons``

For Phase C every entry is a no-op ``1.0 -> 1.0`` worked example. These
tests cover both the contract (idempotence, unknown stores pass
through, missing ``schema_version`` is treated as ``1.0``) and the
recall-pipeline integration so callers see migrated records.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.memory.calibration_memory import recall_calibration
from agentic_swmm.memory.negative_lessons import (
    recall_negative_lessons,
)
from agentic_swmm.memory.parametric_memory import recall_parametric
from agentic_swmm.memory.version_compat import (
    MIGRATIONS,
    migrate_record,
)


class RegistryShapeTests(unittest.TestCase):
    def test_registry_covers_three_stores(self) -> None:
        self.assertIn("parametric_memory", MIGRATIONS)
        self.assertIn("calibration_memory", MIGRATIONS)
        self.assertIn("negative_lessons", MIGRATIONS)

    def test_each_store_has_at_least_one_migration(self) -> None:
        for store, fns in MIGRATIONS.items():
            self.assertGreaterEqual(len(fns), 1, msg=f"empty migration list for {store}")


class MigrateRecordTests(unittest.TestCase):
    def test_identity_migration_preserves_payload(self) -> None:
        row = {
            "schema_version": "1.0",
            "run_id": "r1",
            "case_name": "c1",
        }
        migrated = migrate_record("parametric_memory", row)
        self.assertEqual(migrated["run_id"], "r1")
        self.assertEqual(migrated["case_name"], "c1")
        self.assertEqual(migrated["schema_version"], "1.0")

    def test_missing_schema_version_treated_as_1_0(self) -> None:
        row = {"run_id": "r1", "case_name": "c1"}
        migrated = migrate_record("parametric_memory", row)
        self.assertEqual(migrated["schema_version"], "1.0")

    def test_unknown_store_passes_record_through(self) -> None:
        row = {"foo": "bar"}
        migrated = migrate_record("not_a_real_store", row)
        self.assertEqual(migrated, {"foo": "bar"})

    def test_returns_new_dict_does_not_mutate_input(self) -> None:
        row = {"schema_version": "1.0", "run_id": "r1", "case_name": "c1"}
        snapshot = dict(row)
        migrate_record("parametric_memory", row)
        self.assertEqual(row, snapshot)

    def test_idempotent_under_repeated_migration(self) -> None:
        row = {"schema_version": "1.0", "run_id": "r1", "case_name": "c1"}
        once = migrate_record("parametric_memory", row)
        twice = migrate_record("parametric_memory", once)
        self.assertEqual(once, twice)

    def test_non_dict_record_passed_through(self) -> None:
        # Defensive: a torn line might (in principle) parse to a non-dict.
        # We accept ``None`` rather than raising — recall verbs skip non-
        # dict rows themselves.
        self.assertIsNone(migrate_record("parametric_memory", None))  # type: ignore[arg-type]

    def test_all_three_known_stores_migrate(self) -> None:
        for store in ("parametric_memory", "calibration_memory", "negative_lessons"):
            row = {"schema_version": "1.0", "run_id": "r", "case_name": "c"}
            out = migrate_record(store, row)
            self.assertEqual(out["schema_version"], "1.0")


class RecallPipelineIntegrationTests(unittest.TestCase):
    """A row written by an older schema must still surface from recall.

    For Phase C "older" just means a row with no ``schema_version``
    field. The migration registry must back-fill it so consumers see
    a fully-populated current-schema dict.
    """

    def _write_row(self, store: Path, row: dict[str, Any]) -> None:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def test_parametric_recall_back_fills_schema_version(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            self._write_row(
                store,
                {"run_id": "r1", "case_name": "c1"},  # no schema_version
            )
            rows = recall_parametric(store, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("schema_version"), "1.0")

    def test_calibration_recall_back_fills_schema_version(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            self._write_row(
                store,
                {"run_id": "r1", "case_name": "c1"},
            )
            rows = recall_calibration(store, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("schema_version"), "1.0")

    def test_negative_lessons_recall_back_fills_schema_version(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "negative_lessons.jsonl"
            self._write_row(
                store,
                {
                    "run_id": "r1",
                    "case_name": "c1",
                    "lesson_type": "continuity_fail",
                    "parameters_tried": {"manning_n": 0.013},
                    "metric_observed": {"runoff_continuity_pct": 12.4},
                    "note": "",
                    "recorded_at": "2026-05-19T00:00:00Z",
                },
            )
            lessons = recall_negative_lessons(store, {})
        self.assertEqual(len(lessons), 1)
        self.assertEqual(lessons[0].run_id, "r1")


if __name__ == "__main__":
    unittest.main()
