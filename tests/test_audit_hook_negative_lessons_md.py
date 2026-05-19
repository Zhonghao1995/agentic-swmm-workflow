"""Audit-hook negative-lessons.md bridge (Round 7).

When the audit hook records a FAIL continuity run AND
``negative_lessons.md`` exists in the memory dir, the new lesson must
land in the markdown store (NOT the legacy JSONL). With only the
JSONL on disk, the legacy path stays as the back-compat fallback.

Also covers the ``is_param_set_known_bad`` shim — when the markdown
sibling exists, the helper prefers it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.memory.audit_hook import trigger_memory_refresh
from agentic_swmm.memory.negative_lessons import (
    NegativeLesson,
    is_param_set_known_bad,
    record_negative_lesson,
)
from agentic_swmm.memory.negative_lessons_markdown import (
    NegativeLessonMd,
    add_negative_lesson,
    list_negative_lessons,
)


_FAIL_PROVENANCE = {
    "schema_version": "1.1",
    "run_id": "20260519-143022_urbancase",
    "case_name": "saanich-b8",
    "workflow_mode": "prepared_inp_cli",
    "status": "ok",
    "tools": {
        "python_executable": "/usr/bin/python3",
        "swmm5_version": "5.2.4",
    },
    "metrics": {
        "continuity_error": {
            "values": {"runoff": 15.0, "flow": 7.5},
        },
    },
    "parameters": {"manning_n_overland": 0.25},
}


class AuditHookMdBridgeTests(unittest.TestCase):
    def _make_run(self, project_root: Path) -> Path:
        run_dir = project_root / "runs" / "abc"
        run_dir.mkdir(parents=True)
        (run_dir / "09_audit").mkdir()
        (run_dir / "09_audit" / "experiment_provenance.json").write_text(
            json.dumps(_FAIL_PROVENANCE), encoding="utf-8"
        )
        return run_dir

    def test_md_present_writes_to_md(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            run_dir = self._make_run(project_root)
            memory_dir = project_root / "memory" / "modeling-memory"
            memory_dir.mkdir(parents=True)

            # Pre-create the markdown store so the bridge picks it.
            md_path = memory_dir / "negative_lessons.md"
            add_negative_lesson(
                md_path,
                NegativeLessonMd(
                    name="continuity_fail_other_case",
                    case="other-case",
                    lesson_type="continuity_fail",
                ),
            )

            with mock.patch(
                "agentic_swmm.memory.audit_hook._resolve_memory_dir",
                return_value=memory_dir,
            ):
                trigger_memory_refresh(run_dir=run_dir)

            lessons = list_negative_lessons(md_path)
            self.assertTrue(
                any(lesson.case == "saanich-b8" for lesson in lessons),
                f"new lesson missing from md: {[l.name for l in lessons]}",
            )
            # JSONL fallback file should NOT have been created.
            self.assertFalse((memory_dir / "negative_lessons.jsonl").is_file())

    def test_md_absent_writes_to_jsonl(self) -> None:
        with TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            run_dir = self._make_run(project_root)
            memory_dir = project_root / "memory" / "modeling-memory"
            memory_dir.mkdir(parents=True)

            with mock.patch(
                "agentic_swmm.memory.audit_hook._resolve_memory_dir",
                return_value=memory_dir,
            ):
                trigger_memory_refresh(run_dir=run_dir)

            self.assertTrue((memory_dir / "negative_lessons.jsonl").is_file())
            self.assertFalse((memory_dir / "negative_lessons.md").is_file())


class IsParamSetKnownBadShimTests(unittest.TestCase):
    def test_shim_prefers_md_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            jsonl = tmp_path / "negative_lessons.jsonl"
            md = tmp_path / "negative_lessons.md"

            # JSONL has a row at a different param value — if the shim
            # falls back to JSONL it would NOT match the candidate
            # within 5% tolerance.
            record_negative_lesson(
                jsonl,
                NegativeLesson(
                    run_id="r1",
                    case_name="saanich-b8",
                    lesson_type="continuity_fail",
                    parameters_tried={"manning_n_overland": 0.50},
                ),
            )
            # MD has the exact match.
            add_negative_lesson(
                md,
                NegativeLessonMd(
                    name="continuity_fail_saanich_b8",
                    case="saanich-b8",
                    lesson_type="continuity_fail",
                    parameters_tried={"manning_n_overland": 0.25},
                ),
            )

            match = is_param_set_known_bad(
                jsonl,
                "saanich-b8",
                {"manning_n_overland": 0.255},
                tolerance_pct=5.0,
            )
            self.assertIsNotNone(match)
            assert match is not None
            self.assertEqual("saanich-b8", match.case_name)


if __name__ == "__main__":
    unittest.main()
