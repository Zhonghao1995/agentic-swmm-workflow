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
