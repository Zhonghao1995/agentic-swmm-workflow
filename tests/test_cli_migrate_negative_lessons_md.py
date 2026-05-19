"""``aiswmm memory migrate-negative-lessons-md`` CLI smoke test."""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import main as cli_main
from agentic_swmm.memory.negative_lessons import (
    NegativeLesson,
    record_negative_lesson,
)


class CliMigrateNegativeLessonsMdTests(unittest.TestCase):
    def test_migration_runs_via_cli(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            memory_dir = tmp_path / "memory" / "modeling-memory"
            memory_dir.mkdir(parents=True)
            jsonl = memory_dir / "negative_lessons.jsonl"
            record_negative_lesson(
                jsonl,
                NegativeLesson(
                    run_id="r1",
                    case_name="saanich-b8",
                    lesson_type="continuity_fail",
                    parameters_tried={"manning_n_overland": 0.25},
                ),
            )

            prev_memory_dir = os.environ.get("AISWMM_MEMORY_DIR")
            os.environ["AISWMM_MEMORY_DIR"] = str(memory_dir)
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    code = cli_main(
                        ["memory", "migrate-negative-lessons-md"]
                    )
            finally:
                if prev_memory_dir is not None:
                    os.environ["AISWMM_MEMORY_DIR"] = prev_memory_dir
                else:
                    os.environ.pop("AISWMM_MEMORY_DIR", None)

            self.assertEqual(0, code)
            payload = json.loads(buf.getvalue())
            self.assertEqual(1, payload["migrated"])
            self.assertTrue((memory_dir / "negative_lessons.md").is_file())


if __name__ == "__main__":
    unittest.main()
