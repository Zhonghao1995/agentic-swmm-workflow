"""``aiswmm runs tidy`` (ADR-0004 follow-up): archive, never delete.

Selection rules under test: audited runs never move (their provenance
is cited evidence), recently-touched runs never move (newest mtime in
the TREE, not the dir's own mtime), everything else moves to
``runs/archive/agent/`` with collision bumps, and ``--dry-run`` moves
nothing while reporting exactly what a real run would do.
"""
from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.commands.runs_tidy import tidy_agent_runs

_NOW = 1_800_000_000.0
_OLD = _NOW - 90 * 86400
_FRESH = _NOW - 1 * 86400


def _set_tree_mtime(path: Path, mtime: float) -> None:
    for entry in [path, *path.rglob("*")]:
        os.utime(entry, (mtime, mtime))


class TidySelectionTests(unittest.TestCase):
    def _seed(self, runs_root: Path) -> dict[str, Path]:
        agent = runs_root / "agent"
        dirs = {}
        # Stale + unaudited: the archive candidate.
        stale = agent / "stale-run"
        (stale / "06_runner").mkdir(parents=True)
        (stale / "06_runner" / "model.rpt").write_text("x", encoding="utf-8")
        dirs["stale"] = stale
        # Stale but AUDITED (canonical stage name): must stay.
        audited = agent / "audited-run"
        (audited / run_layout.AUDIT).mkdir(parents=True)
        (audited / run_layout.AUDIT / "experiment_provenance.json").write_text(
            "{}", encoding="utf-8"
        )
        dirs["audited"] = audited
        # Stale but audited under the LEGACY audit name: must also stay.
        legacy = agent / "legacy-audited-run"
        (legacy / "06_audit").mkdir(parents=True)
        (legacy / "06_audit" / "experiment_provenance.json").write_text(
            "{}", encoding="utf-8"
        )
        dirs["legacy_audited"] = legacy
        # Old dir mtime but a FRESH file deep inside: must stay (tree mtime).
        deep_fresh = agent / "deep-fresh-run"
        (deep_fresh / "06_runner").mkdir(parents=True)
        deep_file = deep_fresh / "06_runner" / "model.out"
        deep_file.write_text("x", encoding="utf-8")
        dirs["deep_fresh"] = deep_fresh
        # A stray file at the agent root: ignored, never touched.
        (agent / "notes.txt").write_text("keep me", encoding="utf-8")

        for d in (stale, audited, legacy, deep_fresh):
            _set_tree_mtime(d, _OLD)
        os.utime(deep_file, (_FRESH, _FRESH))
        return dirs

    def test_selection_and_move(self) -> None:
        with TemporaryDirectory() as raw:
            runs_root = Path(raw)
            self._seed(runs_root)
            report = tidy_agent_runs(runs_root, days=30, now=_NOW)

        self.assertEqual([m["name"] for m in report["moved"]], ["stale-run"])
        self.assertCountEqual(
            report["kept_audited"], ["audited-run", "legacy-audited-run"]
        )
        self.assertEqual(report["kept_recent"], ["deep-fresh-run"])

    def test_bytes_move_verbatim_and_nothing_is_deleted(self) -> None:
        with TemporaryDirectory() as raw:
            runs_root = Path(raw)
            self._seed(runs_root)
            tidy_agent_runs(runs_root, days=30, now=_NOW)
            archived = runs_root / "archive" / "agent" / "stale-run"
            self.assertTrue((archived / "06_runner" / "model.rpt").is_file())
            self.assertFalse((runs_root / "agent" / "stale-run").exists())
            # Untouched neighbours.
            self.assertTrue((runs_root / "agent" / "audited-run").exists())
            self.assertTrue((runs_root / "agent" / "notes.txt").exists())

    def test_dry_run_moves_nothing(self) -> None:
        with TemporaryDirectory() as raw:
            runs_root = Path(raw)
            self._seed(runs_root)
            report = tidy_agent_runs(runs_root, days=30, dry_run=True, now=_NOW)
            self.assertEqual(len(report["moved"]), 1)
            self.assertTrue((runs_root / "agent" / "stale-run").exists())
            self.assertFalse((runs_root / "archive").exists())

    def test_archive_collision_bumps(self) -> None:
        with TemporaryDirectory() as raw:
            runs_root = Path(raw)
            self._seed(runs_root)
            occupied = runs_root / "archive" / "agent" / "stale-run"
            occupied.mkdir(parents=True)
            report = tidy_agent_runs(runs_root, days=30, now=_NOW)
            self.assertEqual(report["moved"][0]["to"], str(occupied.parent / "stale-run-2"))
            self.assertTrue((occupied.parent / "stale-run-2" / "06_runner").is_dir())

    def test_missing_agent_root_is_a_noop(self) -> None:
        with TemporaryDirectory() as raw:
            report = tidy_agent_runs(Path(raw), days=30, now=_NOW)
        self.assertEqual(report["moved"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
