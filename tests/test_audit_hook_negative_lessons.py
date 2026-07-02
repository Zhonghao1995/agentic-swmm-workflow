"""Structural test for the audit-hook -> negative_lessons wiring
(PRD-06 Phase C.2).

When ``trigger_memory_refresh`` runs on a run whose provenance reports
continuity values in the FAIL band, a row must land in
``memory/modeling-memory/negative_lessons.jsonl`` AND the parametric
record must also be present (the parametric record is the gating
condition per spec).

Conversely, runs that PASS continuity must not pollute the
negative-lessons store.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.memory.audit_hook import trigger_memory_refresh
from agentic_swmm.memory.negative_lessons import recall_negative_lessons
from tests.conftest import patched_audit_hook_subprocess, seed_provenance_run_dir


_BASE_PROVENANCE = {
    "schema_version": "1.1",
    "run_id": "20260519-143022_urbancase",
    "case_name": "saanich-b8",
    "workflow_mode": "prepared_inp_cli",
    "status": "ok",
    "tools": {
        "python_executable": "/usr/bin/python3",
        "swmm5_version": "5.2.4",
    },
}


class AuditHookNegativeLessonsTests(unittest.TestCase):
    def test_continuity_fail_writes_negative_lesson(self) -> None:
        prov = dict(_BASE_PROVENANCE)
        prov["metrics"] = {
            "continuity_error": {
                "name": "continuity_error",
                "values": {"runoff": -15.2, "flow": 0.04},  # runoff FAIL band
            }
        }
        prov["calibration"] = {
            "parameters": {"manning_n": 0.013, "imdmax": 0.25},
        }
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = seed_provenance_run_dir(project_root, prov)
            with patched_audit_hook_subprocess():
                result = trigger_memory_refresh(run_dir)

            self.assertFalse(result["skipped"], msg=str(result))
            self.assertIn("negative_lessons", result, msg=str(result))
            store = Path(result["negative_lessons"])
            self.assertTrue(store.is_file())
            lessons = recall_negative_lessons(store, {})
        self.assertEqual(len(lessons), 1)
        lesson = lessons[0]
        self.assertEqual(lesson.run_id, prov["run_id"])
        self.assertEqual(lesson.case_name, prov["case_name"])
        self.assertEqual(lesson.lesson_type, "continuity_fail")
        self.assertEqual(lesson.parameters_tried["manning_n"], 0.013)
        self.assertAlmostEqual(
            lesson.metric_observed["runoff_continuity_pct"], -15.2, places=3
        )
        self.assertIn("runoff", lesson.note)

    def test_continuity_pass_writes_no_negative_lesson(self) -> None:
        prov = dict(_BASE_PROVENANCE)
        prov["metrics"] = {
            "continuity_error": {
                "name": "continuity_error",
                "values": {"runoff": -0.18, "flow": 0.04},  # both PASS
            }
        }
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = seed_provenance_run_dir(project_root, prov)
            with patched_audit_hook_subprocess():
                result = trigger_memory_refresh(run_dir)
        self.assertNotIn("negative_lessons", result, msg=str(result))

    def test_skipped_run_writes_no_negative_lesson(self) -> None:
        prov = dict(_BASE_PROVENANCE)
        prov["metrics"] = {
            "continuity_error": {
                "name": "continuity_error",
                "values": {"runoff": -15.2},
            }
        }
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = seed_provenance_run_dir(project_root, prov)
            result = trigger_memory_refresh(run_dir, no_memory=True)
        self.assertTrue(result["skipped"])
        self.assertNotIn("negative_lessons", result)

    def test_audit_pipeline_survives_negative_lesson_write_error(self) -> None:
        """A buggy negative-lesson writer must not block the audit pipeline."""
        prov = dict(_BASE_PROVENANCE)
        prov["metrics"] = {
            "continuity_error": {
                "name": "continuity_error",
                "values": {"runoff": -15.2},
            }
        }
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "proj"
            project_root.mkdir()
            run_dir = seed_provenance_run_dir(project_root, prov)
            with patched_audit_hook_subprocess(), mock.patch(
                "agentic_swmm.memory.audit_hook._record_negative_lesson_for_continuity_fail",
                side_effect=RuntimeError("boom"),
            ):
                result = trigger_memory_refresh(run_dir)
        self.assertFalse(result["skipped"], msg=str(result))
        # The rest of the audit pipeline still completed.
        self.assertIn("parametric_memory", result)
        # The failure is captured in errors but does not abort.
        self.assertTrue(
            any("negative lesson" in e for e in result["errors"]), msg=str(result)
        )


if __name__ == "__main__":
    unittest.main()
