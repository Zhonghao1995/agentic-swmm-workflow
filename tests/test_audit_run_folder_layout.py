"""Tests for agentic_swmm.audit.run_folder_layout.

The PRD requires this module to be a pure-function layer:
  - RunKind enum with CHAT, SWMM, ZOMBIE
  - validate(run_dir) -> ValidationResult
  - discover(runs_root) walks the tree BFS, unlimited depth
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class RunFolderLayoutImportTests(unittest.TestCase):
    def test_module_exposes_runkind_validationresult_validate_discover(self) -> None:
        from agentic_swmm.audit import run_folder_layout as rfl

        self.assertTrue(hasattr(rfl, "RunKind"))
        self.assertTrue(hasattr(rfl, "ValidationResult"))
        self.assertTrue(callable(rfl.validate))
        self.assertTrue(callable(rfl.discover))
        # RunKind values per PRD.
        self.assertEqual({m.name for m in rfl.RunKind}, {"CHAT", "SWMM", "ZOMBIE"})


class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make_chat(self, path: Path, *, with_note: bool) -> None:
        path.mkdir(parents=True, exist_ok=True)
        (path / "session_state.json").write_text("{}", encoding="utf-8")
        (path / "agent_trace.jsonl").write_text("", encoding="utf-8")
        if with_note:
            (path / "chat_note.md").write_text("---\ntype: chat-session\n---\n", encoding="utf-8")

    def _make_swmm_audited(self, path: Path) -> None:
        audit = path / "09_audit"
        audit.mkdir(parents=True, exist_ok=True)
        (audit / "experiment_note.md").write_text("---\ntype: experiment-audit\n---\n", encoding="utf-8")
        (audit / "experiment_provenance.json").write_text(
            json.dumps({"schema_version": "1.1", "status": "pass"}), encoding="utf-8"
        )

    def _make_swmm_unaudited(self, path: Path) -> None:
        # Has stage dirs but no 09_audit/.
        (path / "05_builder").mkdir(parents=True, exist_ok=True)
        (path / "06_runner").mkdir(parents=True, exist_ok=True)
        (path / "manifest.json").write_text("{}", encoding="utf-8")

    def test_valid_chat_dir(self) -> None:
        from agentic_swmm.audit.run_folder_layout import RunKind, validate

        chat = self.root / "2026-05-13" / "100000_demo_chat"
        self._make_chat(chat, with_note=True)
        result = validate(chat)
        self.assertEqual(result.kind, RunKind.CHAT)
        self.assertTrue(result.ok)
        self.assertEqual(result.missing, [])

    def test_chat_dir_missing_required_file(self) -> None:
        from agentic_swmm.audit.run_folder_layout import RunKind, validate

        chat = self.root / "2026-05-13" / "100100_demo_chat"
        self._make_chat(chat, with_note=False)
        result = validate(chat)
        self.assertEqual(result.kind, RunKind.CHAT)
        self.assertFalse(result.ok)
        self.assertIn("chat_note.md", result.missing)

    def test_valid_swmm_dir(self) -> None:
        from agentic_swmm.audit.run_folder_layout import RunKind, validate

        run = self.root / "real-todcreek-minimal"
        self._make_swmm_audited(run)
        result = validate(run)
        self.assertEqual(result.kind, RunKind.SWMM)
        self.assertTrue(result.ok)

    def test_swmm_dir_with_old_root_layout_is_invalid(self) -> None:
        """A run dir with experiment_note.md at root (legacy P1/P2/P3) must not validate."""
        from agentic_swmm.audit.run_folder_layout import RunKind, validate

        run = self.root / "legacy"
        run.mkdir(parents=True, exist_ok=True)
        (run / "experiment_note.md").write_text("legacy\n", encoding="utf-8")
        (run / "experiment_provenance.json").write_text("{}", encoding="utf-8")
        (run / "05_builder").mkdir(parents=True, exist_ok=True)
        result = validate(run)
        self.assertEqual(result.kind, RunKind.SWMM)
        self.assertFalse(result.ok)
        self.assertIn("09_audit/experiment_note.md", result.missing)

    def test_zombie_dir_kind(self) -> None:
        """A bare runs/agent/agent-<ts>/ with no recognizable run/chat content is ZOMBIE."""
        from agentic_swmm.audit.run_folder_layout import RunKind, validate

        zombie = self.root / "agent" / "agent-1778717638"
        zombie.mkdir(parents=True, exist_ok=True)
        (zombie / "agent_trace.jsonl").write_text("", encoding="utf-8")
        result = validate(zombie)
        self.assertEqual(result.kind, RunKind.ZOMBIE)
        self.assertFalse(result.ok)


class DiscoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_discover_walks_unlimited_depth_bfs(self) -> None:
        from agentic_swmm.audit.run_folder_layout import RunKind, discover

        # Top-level SWMM run.
        top = self.root / "real-todcreek-minimal"
        (top / "09_audit").mkdir(parents=True)
        (top / "09_audit" / "experiment_note.md").write_text("x", encoding="utf-8")
        (top / "09_audit" / "experiment_provenance.json").write_text("{}", encoding="utf-8")

        # Bucket-level SWMM run.
        bucket = self.root / "benchmarks" / "tecnopolo"
        (bucket / "09_audit").mkdir(parents=True)
        (bucket / "09_audit" / "experiment_note.md").write_text("x", encoding="utf-8")
        (bucket / "09_audit" / "experiment_provenance.json").write_text("{}", encoding="utf-8")

        # 3-level nested SWMM run.
        deep = self.root / "external-case-candidates" / "zenodo" / "month-1" / "runner"
        (deep / "09_audit").mkdir(parents=True)
        (deep / "09_audit" / "experiment_note.md").write_text("x", encoding="utf-8")
        (deep / "09_audit" / "experiment_provenance.json").write_text("{}", encoding="utf-8")

        # Chat dir.
        chat = self.root / "2026-05-13" / "100000_demo_chat"
        chat.mkdir(parents=True)
        (chat / "session_state.json").write_text("{}", encoding="utf-8")
        (chat / "agent_trace.jsonl").write_text("", encoding="utf-8")
        (chat / "chat_note.md").write_text("---\n---\n", encoding="utf-8")

        # Unaudited run dir (has stage dirs but no 09_audit).
        unaudited = self.root / "raw-case-candidates" / "case-x"
        (unaudited / "05_builder").mkdir(parents=True)
        (unaudited / "manifest.json").write_text("{}", encoding="utf-8")

        # Archived zombies should not be yielded.
        archived = self.root / ".archive" / "agent-1778717638"
        archived.mkdir(parents=True)

        found = list(discover(self.root))
        paths = {rf.path: rf.kind for rf in found}

        self.assertIn(top, paths)
        self.assertIn(bucket, paths)
        self.assertIn(deep, paths)
        self.assertIn(chat, paths)
        self.assertEqual(paths[top], RunKind.SWMM)
        self.assertEqual(paths[chat], RunKind.CHAT)
        # Unaudited SWMM run is still discovered (kind=SWMM, validate fails).
        self.assertIn(unaudited, paths)
        self.assertEqual(paths[unaudited], RunKind.SWMM)
        # Archived dirs are excluded.
        for path in paths:
            self.assertNotIn(".archive", path.parts)

    def test_discover_does_not_recurse_into_run_dirs(self) -> None:
        """Inside an audited SWMM run, subdirs are not re-yielded as separate run dirs."""
        from agentic_swmm.audit.run_folder_layout import discover

        run = self.root / "case-y"
        (run / "09_audit").mkdir(parents=True)
        (run / "09_audit" / "experiment_note.md").write_text("x", encoding="utf-8")
        (run / "09_audit" / "experiment_provenance.json").write_text("{}", encoding="utf-8")
        (run / "05_builder").mkdir(parents=True)
        (run / "06_runner").mkdir(parents=True)

        found = list(discover(self.root))
        self.assertEqual([rf.path for rf in found], [run])


if __name__ == "__main__":
    unittest.main()
