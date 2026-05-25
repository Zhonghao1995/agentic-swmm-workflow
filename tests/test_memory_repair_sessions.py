"""``aiswmm memory repair-sessions`` verb (issue #204).

Tests the non-destructive repair path:

1. The corrupt DB is moved to ``sessions.sqlite.corrupt-<timestamp>``
   before any rebuild starts (never destructive).
2. A fresh DB is rebuilt at the original path from
   ``runs/*/agent_trace.jsonl`` plus the sibling
   ``session_state.json`` files.
3. Both the CLI entry point (``aiswmm memory repair-sessions``) and
   the underlying ``repair_sessions_db`` helper are exercised.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory


def _seed_intact_db(db_path: Path) -> None:
    from agentic_swmm.memory import session_db

    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id="placeholder",
            start_utc="2026-05-24T12:00:00+00:00",
            end_utc="2026-05-24T12:00:30+00:00",
            goal="placeholder",
            case_name="demo",
            planner="openai",
            model="gpt-5",
            ok=True,
        )
        for step in range(1, 8):
            session_db.insert_message(
                conn,
                session_id="placeholder",
                step=step,
                role="user" if step % 2 else "assistant",
                text=f"placeholder filler {step} " * 80,
                utc="2026-05-24T12:00:01+00:00",
            )
        conn.commit()


def _corrupt(db_path: Path) -> None:
    raw = bytearray(db_path.read_bytes())
    for i in range(200, len(raw) - 8):
        raw[i] = 0xAA
    db_path.write_bytes(bytes(raw))


def _seed_session_dir(
    runs_dir: Path,
    *,
    date: str,
    leaf: str,
    case_name: str,
    user_text: str,
    assistant_text: str,
) -> Path:
    """Write the minimal session_state.json + agent_trace.jsonl pair
    the live sync projector needs to rebuild a row.
    """
    session_dir = runs_dir / date / leaf
    session_dir.mkdir(parents=True)
    (session_dir / "session_state.json").write_text(
        json.dumps(
            {
                "case_name": case_name,
                "goal": "rebuild me",
                "planner": "openai",
                "model": "gpt-5",
                "ok": True,
                "status": "ok",
            }
        ),
        encoding="utf-8",
    )
    events = [
        {
            "event": "session_start",
            "timestamp_utc": "2026-05-24T10:00:00+00:00",
            "goal": "rebuild me",
        },
        {
            "event": "user_prompt",
            "timestamp_utc": "2026-05-24T10:00:01+00:00",
            "text": user_text,
        },
        {
            "event": "assistant_text",
            "timestamp_utc": "2026-05-24T10:00:02+00:00",
            "text": assistant_text,
        },
    ]
    with (session_dir / "agent_trace.jsonl").open("w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return session_dir


# ---------------------------------------------------------------------------
# Helper API
# ---------------------------------------------------------------------------


class RepairSessionsHelperTests(unittest.TestCase):
    def test_repair_backs_up_corrupt_db_with_timestamp_suffix(self) -> None:
        from agentic_swmm.commands.memory import repair_sessions_db
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            corrupt_bytes = db_path.read_bytes()
            _seed_session_dir(
                runs_dir,
                date="2026-05-24",
                leaf="100000_demo_run",
                case_name="demo",
                user_text="hi",
                assistant_text="hello",
            )

            result = repair_sessions_db(runs_dir, db_path=db_path)

            self.assertEqual(result["ok"], True)
            backup = Path(result["backup"])
            self.assertTrue(backup.exists())
            # Filename pattern: sessions.sqlite.corrupt-YYYYMMDDTHHMMSSZ
            self.assertRegex(
                backup.name,
                r"^sessions\.sqlite\.corrupt-\d{8}T\d{6}Z$",
            )
            # The backup carries the exact bytes of the corrupt original.
            self.assertEqual(backup.read_bytes(), corrupt_bytes)

    def test_repair_rebuilds_db_from_session_trace(self) -> None:
        from agentic_swmm.commands.memory import repair_sessions_db
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            _seed_session_dir(
                runs_dir,
                date="2026-05-24",
                leaf="100000_demo_run",
                case_name="demo",
                user_text="rebuild question",
                assistant_text="rebuild answer",
            )
            _seed_session_dir(
                runs_dir,
                date="2026-05-23",
                leaf="090000_other_run",
                case_name="other",
                user_text="other prompt",
                assistant_text="other reply",
            )

            result = repair_sessions_db(runs_dir, db_path=db_path)

            self.assertEqual(result["ok"], True)
            # The rebuilt DB must pass integrity_check.
            session_db.clear_integrity_cache()
            report = session_db.integrity_check(db_path)
            self.assertEqual(report.state, "ok")
            # Both session traces were ingested.
            with session_db.connect(db_path) as conn:
                ids = session_db.list_session_ids(conn)
            self.assertEqual(len(ids), 2)
            self.assertEqual(result["sessions_rebuilt"], 2)

    def test_repair_when_db_absent_just_rebuilds(self) -> None:
        """Calling repair on a runs/ directory with no sessions.sqlite
        yet should still walk the traces and create a fresh store. No
        backup file is written (nothing to back up).
        """
        from agentic_swmm.commands.memory import repair_sessions_db
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            self.assertFalse(db_path.exists())
            session_db.clear_integrity_cache()
            _seed_session_dir(
                runs_dir,
                date="2026-05-24",
                leaf="100000_demo_run",
                case_name="demo",
                user_text="fresh hi",
                assistant_text="fresh hello",
            )

            result = repair_sessions_db(runs_dir, db_path=db_path)

            self.assertEqual(result["ok"], True)
            self.assertIsNone(result.get("backup"))
            self.assertTrue(db_path.exists())
            session_db.clear_integrity_cache()
            report = session_db.integrity_check(db_path)
            self.assertEqual(report.state, "ok")

    def test_repair_does_not_overwrite_corrupt_without_backup(self) -> None:
        """Hard constraint from the issue: the verb must always back
        the corrupt file up before touching it. Even if no traces are
        found, the original corrupt file must remain accessible at the
        backup path.
        """
        from agentic_swmm.commands.memory import repair_sessions_db
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            corrupt_bytes = db_path.read_bytes()

            result = repair_sessions_db(runs_dir, db_path=db_path)

            backup = Path(result["backup"])
            self.assertTrue(backup.exists())
            self.assertEqual(backup.read_bytes(), corrupt_bytes)
            # The original path is replaced by a freshly-initialised DB,
            # never deleted.
            self.assertTrue(db_path.exists())

    def test_repair_returns_friendly_error_when_backup_fails(self) -> None:
        """Issue #212 item #4: when ``os.replace`` raises (disk full,
        permission denied, cross-FS), the user must see a friendly
        message instead of an unhandled traceback. ``ok`` stays False
        and the original DB stays untouched.
        """
        import os as _os
        from unittest import mock

        from agentic_swmm.commands.memory import repair_sessions_db
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            corrupt_bytes = db_path.read_bytes()

            with mock.patch.object(
                _os, "replace", side_effect=OSError("disk full")
            ):
                result = repair_sessions_db(runs_dir, db_path=db_path)

            self.assertEqual(result["ok"], False)
            self.assertIsNone(result.get("backup"))
            # One friendly failure entry mentioning the cause.
            failures = result.get("failures") or []
            self.assertTrue(failures)
            joined = " ".join(failures)
            self.assertIn("could not back up", joined)
            self.assertIn("disk full", joined)
            self.assertIn("aborting repair", joined)
            # Original file untouched.
            self.assertEqual(db_path.read_bytes(), corrupt_bytes)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


class RepairSessionsCLITests(unittest.TestCase):
    def test_cli_repair_sessions_invokes_helper_and_prints_summary(self) -> None:
        from agentic_swmm.cli import main as cli_main
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            _seed_session_dir(
                runs_dir,
                date="2026-05-24",
                leaf="100000_demo_run",
                case_name="demo",
                user_text="rebuild?",
                assistant_text="rebuilt.",
            )

            buf = io.StringIO()
            import os

            os.environ["AISWMM_RUNS_ROOT"] = str(runs_dir)
            try:
                with redirect_stdout(buf):
                    # ``--yes`` skips the new interactive prompt
                    # (issue #212). Without it the CLI now refuses on
                    # non-interactive stdin to protect users from
                    # accidental destructive runs in scripts.
                    rc = cli_main(["memory", "repair-sessions", "--yes"])
            finally:
                os.environ.pop("AISWMM_RUNS_ROOT", None)

        self.assertEqual(rc, 0)
        body = buf.getvalue()
        # Summary mentions both the backup filename and the rebuild count.
        self.assertIn("sessions.sqlite.corrupt-", body)
        self.assertIn("rebuilt", body.lower())

    def test_cli_repair_sessions_dry_run_writes_nothing(self) -> None:
        """Issue #212 item #1: ``--dry-run`` prints the would-be plan
        without touching disk. Both the corrupt DB and the absence of
        a backup file are guaranteed.
        """
        from agentic_swmm.cli import main as cli_main
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            corrupt_bytes = db_path.read_bytes()
            _seed_session_dir(
                runs_dir,
                date="2026-05-24",
                leaf="100000_demo_run",
                case_name="demo",
                user_text="dry run",
                assistant_text="dry reply",
            )

            buf = io.StringIO()
            import os

            os.environ["AISWMM_RUNS_ROOT"] = str(runs_dir)
            try:
                with redirect_stdout(buf):
                    rc = cli_main(
                        ["memory", "repair-sessions", "--dry-run"]
                    )
            finally:
                os.environ.pop("AISWMM_RUNS_ROOT", None)

            self.assertEqual(rc, 0)
            body = buf.getvalue()
            self.assertIn("would back up", body)
            self.assertIn("would rebuild", body)
            self.assertIn("dry run", body.lower())
            # File untouched — same bytes as before.
            self.assertEqual(db_path.read_bytes(), corrupt_bytes)
            # No backup file was created.
            backups = list(runs_dir.glob("sessions.sqlite.corrupt-*"))
            self.assertEqual(backups, [])

    def test_cli_repair_sessions_refuses_without_yes_on_non_tty(
        self,
    ) -> None:
        """Issue #212 item #1: without ``--yes`` and without a TTY
        stdin (i.e. scripted invocation) the CLI must refuse rather
        than silently destroy data.
        """
        from agentic_swmm.cli import main as cli_main
        from agentic_swmm.memory import session_db

        with TemporaryDirectory() as tmp:
            runs_dir = Path(tmp)
            db_path = runs_dir / "sessions.sqlite"
            _seed_intact_db(db_path)
            _corrupt(db_path)
            session_db.clear_integrity_cache()
            corrupt_bytes = db_path.read_bytes()

            import io as _io
            import os

            buf_out = _io.StringIO()
            buf_err = _io.StringIO()
            from contextlib import redirect_stderr

            os.environ["AISWMM_RUNS_ROOT"] = str(runs_dir)
            try:
                with redirect_stdout(buf_out), redirect_stderr(buf_err):
                    rc = cli_main(["memory", "repair-sessions"])
            finally:
                os.environ.pop("AISWMM_RUNS_ROOT", None)

            self.assertEqual(rc, 1)
            # Friendly stderr explaining the refusal.
            self.assertIn("refusing", buf_err.getvalue().lower())
            self.assertIn("--yes", buf_err.getvalue())
            # File untouched.
            self.assertEqual(db_path.read_bytes(), corrupt_bytes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
