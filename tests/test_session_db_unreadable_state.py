"""Tests for the ``unreadable`` IntegrityReport state (issue #204 review).

Issue #204 originally only had 3 states (absent / ok / corrupt). Code
review flagged that permission-denied and DB-locked errors were
collapsing into ``corrupt`` — which would lead users to run the
destructive ``aiswmm memory repair-sessions`` verb against a perfectly
healthy database. This module pins the four-state contract:

* ``absent`` — file does not exist (not an error)
* ``ok``     — file opens + PRAGMA reports ok
* ``corrupt`` — PRAGMA fails on a file we could open (real corruption)
* ``unreadable`` — file exists but cannot be opened (permission / lock /
  transient I/O); DB might be healthy; do NOT trigger destructive repair
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentic_swmm.memory import session_db


def _seed_intact(db_path: Path) -> None:
    """Just initialize the schema. We don't need rows for these tests —
    chmod 000 protects the file regardless of contents."""
    session_db.initialize(db_path)


class PermissionDeniedReportsUnreadableTests(unittest.TestCase):
    """A file we cannot open (chmod 000) is ``unreadable``, not ``corrupt``."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "sessions.sqlite"
        _seed_intact(self.db_path)
        # Stash original mode so we can restore on tearDown (cleanup
        # otherwise refuses to delete the file).
        self._original_mode = self.db_path.stat().st_mode
        self.addCleanup(self._restore_mode)
        session_db.clear_integrity_cache()

    def _restore_mode(self) -> None:
        if self.db_path.exists():
            os.chmod(self.db_path, self._original_mode)

    def test_chmod_000_reports_unreadable(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses POSIX file permissions; skip")
        os.chmod(self.db_path, 0)
        report = session_db.integrity_check(self.db_path)
        self.assertEqual(
            report.state,
            "unreadable",
            "permission-denied must NOT be misclassified as corrupt — "
            "that would lead users to overwrite a healthy DB",
        )
        self.assertGreater(len(report.errors), 0)


class CorruptHeaderReportsCorruptTests(unittest.TestCase):
    """A genuinely corrupt file is still ``corrupt`` (not regressed)."""

    def test_garbage_bytes_report_corrupt(self) -> None:
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sessions.sqlite"
            db_path.write_bytes(b"not a sqlite database at all")
            session_db.clear_integrity_cache()
            report = session_db.integrity_check(db_path)
            self.assertEqual(
                report.state,
                "corrupt",
                "real corruption must still be flagged as corrupt; "
                "the unreadable branch should not swallow this",
            )


class RepairRefusesOnUnreadableTests(unittest.TestCase):
    """``repair_sessions_db`` refuses to operate on ``unreadable`` files."""

    def test_repair_refuses_when_db_is_unreadable(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root bypasses POSIX file permissions; skip")
        from agentic_swmm.commands.memory import repair_sessions_db

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "sessions.sqlite"
            _seed_intact(db_path)
            original_mode = db_path.stat().st_mode
            os.chmod(db_path, 0)
            try:
                session_db.clear_integrity_cache()
                summary = repair_sessions_db(tmp_path, db_path=db_path)
            finally:
                os.chmod(db_path, original_mode)

            self.assertFalse(
                summary["ok"],
                "repair must NOT report success on unreadable; "
                "issue #204 review HIGH finding",
            )
            self.assertEqual(
                summary["sessions_rebuilt"],
                0,
                "repair must not have attempted a rebuild",
            )
            self.assertIsNone(
                summary["backup"],
                "repair must not have moved (backed up) a possibly-healthy DB",
            )
            self.assertTrue(
                any("unreadable" in f for f in summary["failures"]),
                "failure reason must mention 'unreadable' so the CLI prints a useful hint",
            )


class RepairOkFlagReflectsFailuresTests(unittest.TestCase):
    """``summary['ok']`` must be False when any session failed (was always True).

    Issue #204 review MEDIUM finding. Verified by injecting a sync
    failure via monkey-patch — the simplest way to guarantee a failure
    without depending on the precise session_state.json schema.
    """

    def test_ok_false_when_sessions_fail(self) -> None:
        import agentic_swmm.commands.memory as memory_mod
        from agentic_swmm.commands.memory import repair_sessions_db

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "sessions.sqlite"
            session_dir = tmp_path / "2026-05-24" / "120000_bad"
            session_dir.mkdir(parents=True)
            (session_dir / "session_state.json").write_text("{}")
            (session_dir / "agent_trace.jsonl").write_text("")

            # Monkey-patch sync_session_to_db to always raise — guarantees
            # the failures list is populated regardless of trace schema.
            from agentic_swmm.memory import session_sync as ss
            original = ss.sync_session_to_db

            def boom(*args, **kwargs):
                raise RuntimeError("simulated failure for test")

            ss.sync_session_to_db = boom
            try:
                summary = repair_sessions_db(tmp_path, db_path=db_path)
            finally:
                ss.sync_session_to_db = original

        self.assertFalse(
            summary["ok"],
            "summary['ok'] must reflect failures — issue #204 review MEDIUM",
        )
        self.assertGreater(
            len(summary["failures"]),
            0,
            "failure must have been recorded",
        )


if __name__ == "__main__":
    unittest.main()
