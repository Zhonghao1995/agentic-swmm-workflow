"""A run root is what a person opens first.

Reported with a screenshot of one: nine loose files, four of which
(``aiswmm_state.json``, ``context_summary.md``, ``session_state.json``,
``report.docx``) were not even in CANONICAL_ROOT_FILES, sitting next to the
one report the reader actually wanted. The machine's notebook and the
deliverable had equal billing.

The sidecars move under ``_agent/``. They are not deleted and not moved into
the sqlite session DB, because ``agent_trace.jsonl`` is the ground truth the
DB is rebuilt from (see session_db and session_repair). They are moved out of
the eyeline.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.run_layout import (
    AGENT_DIR,
    AGENT_FILES,
    CANONICAL_ROOT_FILES,
    agent_file,
    agent_file_for_write,
)


class ResolverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name)

    def test_a_fresh_run_writes_under_the_agent_dir(self) -> None:
        path = agent_file(self.run_dir, "agent_trace.jsonl")
        self.assertEqual(path.parent.name, AGENT_DIR)

    def test_resolving_creates_nothing(self) -> None:
        # An earlier version created _agent/ here, and a caller that only
        # wanted .exists() left an empty directory at whatever path it was
        # handed, including the runs root, where it was counted as a session.
        agent_file(self.run_dir, "agent_trace.jsonl")
        self.assertEqual(list(self.run_dir.iterdir()), [])

    def test_the_write_helper_creates_the_directory(self) -> None:
        path = agent_file_for_write(self.run_dir, "agent_trace.jsonl")
        self.assertTrue(path.parent.is_dir())

    def test_a_legacy_run_keeps_using_its_root_copy(self) -> None:
        # ADR-0004: legacy layouts stay readable forever. An append must land
        # in the file the run already has, or the run is split in two.
        legacy = self.run_dir / "agent_trace.jsonl"
        legacy.write_text('{"step": 1}\n', encoding="utf-8")
        self.assertEqual(agent_file(self.run_dir, "agent_trace.jsonl"), legacy)
        self.assertEqual(agent_file_for_write(self.run_dir, "agent_trace.jsonl"), legacy)

    def test_the_new_location_wins_when_both_exist(self) -> None:
        (self.run_dir / "agent_trace.jsonl").write_text("old\n", encoding="utf-8")
        new = self.run_dir / AGENT_DIR / "agent_trace.jsonl"
        new.parent.mkdir()
        new.write_text("new\n", encoding="utf-8")
        self.assertEqual(agent_file(self.run_dir, "agent_trace.jsonl"), new)

    def test_every_agent_file_is_covered(self) -> None:
        for name in AGENT_FILES:
            self.assertEqual(agent_file(self.run_dir, name).parent.name, AGENT_DIR, name)


class RootContractTests(unittest.TestCase):
    def test_the_audience_directories_are_declared_root_entries(self) -> None:
        self.assertIn(AGENT_DIR, CANONICAL_ROOT_FILES)
        self.assertIn("_obsidian", CANONICAL_ROOT_FILES)

    def test_the_deliverables_are_declared_root_entries(self) -> None:
        for name in ("README.md", "final_report.md", "report.docx", "manifest.json"):
            self.assertIn(name, CANONICAL_ROOT_FILES, name)

    def test_legacy_sidecar_names_stay_valid_at_the_root(self) -> None:
        # Old runs must not become invalid because the layout moved on.
        for name in ("agent_trace.jsonl", "memory_trace.jsonl", "agent_snapshot.json"):
            self.assertIn(name, CANONICAL_ROOT_FILES, name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class RunReadmeTests(unittest.TestCase):
    """A run folder should explain itself to whoever opens it next."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "194142_tecnopolo_run"
        self.run_dir.mkdir()

    def _render(self, **kwargs) -> str:
        from agentic_swmm.reporting.run_readme import render_run_readme

        return render_run_readme(self.run_dir, **kwargs)

    def test_it_lists_only_what_exists(self) -> None:
        # Never advertise a report or a figure that was not produced.
        body = self._render()
        self.assertNotIn("report.docx", body)
        (self.run_dir / "report.docx").write_text("x", encoding="utf-8")
        self.assertIn("report.docx", self._render())

    def test_stages_are_explained_in_words(self) -> None:
        (self.run_dir / "06_runner").mkdir()
        body = self._render()
        self.assertIn("06_runner", body)
        self.assertIn("model.rpt", body)

    def test_the_agent_dir_is_labelled_as_not_a_result(self) -> None:
        (self.run_dir / AGENT_DIR).mkdir()
        body = self._render()
        self.assertIn("nothing here is a result", body)

    def test_the_evidence_boundary_is_always_stated(self) -> None:
        # A run folder is where someone decides whether to trust a number.
        self.assertIn("not a calibrated or validated one", self._render())

    def test_writing_is_best_effort(self) -> None:
        from agentic_swmm.reporting.run_readme import write_run_readme

        self.assertIsNone(write_run_readme(self.run_dir / "does-not-exist"))


class EngineOutputTests(unittest.TestCase):
    """SWMM's console output is provenance, not a result.

    It used to sit beside model.rpt, so opening the runner stage met a wall
    of "hour: 1 [][][][]" progress bars next to the report the reader wanted.
    reporting.py already classified these as CLI-wrapper noise; the layout
    now agrees with that classification.
    """

    def test_the_runner_writes_console_output_into_a_sub_box(self) -> None:
        source = Path("skills/swmm-runner/scripts/swmm_runner.py").read_text(encoding="utf-8")
        self.assertIn('engine_dir = run_dir / "_engine"', source)
        self.assertNotIn('stdout_path = run_dir / "stdout.txt"', source)

    def test_the_report_still_treats_them_as_internal(self) -> None:
        from agentic_swmm.agent.reporting import _PLANNER_INTERNAL_FRAGMENTS

        # Matching is by suffix, so the move does not smuggle them into a
        # user-facing artifact list.
        self.assertIn("/stdout.txt", _PLANNER_INTERNAL_FRAGMENTS)
        self.assertIn("/stderr.txt", _PLANNER_INTERNAL_FRAGMENTS)
