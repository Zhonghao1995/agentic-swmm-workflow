"""Returning-user banner: compact logo + last-session reference.

Issue #57 (UX-2). When ``~/.aiswmm/first_run.json`` is present the
welcome must print a compact header that includes:

- The version + session label + profile (similar to the existing
  one-line banner).
- A "Last session: <relative time> -- case "<case_name>"" line
  read from the cross-session SQLite store (PR #38 SessionDB).
- A tip line listing the slash commands + ``--safe`` flag.

When SessionDB is empty (no prior session ever ended) the line must
degrade gracefully to "No prior session" rather than crash.
"""

from __future__ import annotations

import io
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import welcome
from agentic_swmm.memory import session_db


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _seed_session(
    db_path: Path,
    *,
    session_id: str,
    case_name: str,
    end_utc: str,
    ok: bool = True,
) -> None:
    session_db.initialize(db_path)
    with session_db.connect(db_path) as conn:
        session_db.upsert_session(
            conn,
            session_id=session_id,
            start_utc=end_utc,
            end_utc=end_utc,
            goal="run the demo",
            case_name=case_name,
            planner="openai",
            model="gpt-5.5",
            ok=ok,
        )
        conn.commit()


class LookupLastSessionTests(unittest.TestCase):
    """``lookup_last_session`` returns the most recently-ended row."""

    def test_returns_most_recent_session(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sessions.sqlite"
            old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
            new = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
            _seed_session(db_path, session_id="old", case_name="todcreek", end_utc=old)
            _seed_session(db_path, session_id="new", case_name="tecnopolo", end_utc=new)
            row = welcome.lookup_last_session(db_path=db_path)
            self.assertIsNotNone(row)
            self.assertEqual(row["case_name"], "tecnopolo")
            self.assertEqual(row["session_id"], "new")

    def test_returns_none_when_db_missing(self) -> None:
        row = welcome.lookup_last_session(db_path=Path("/nonexistent/sessions.sqlite"))
        self.assertIsNone(row)

    def test_returns_none_when_db_empty(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sessions.sqlite"
            session_db.initialize(db_path)
            self.assertIsNone(welcome.lookup_last_session(db_path=db_path))

    def test_ignores_sessions_with_null_end_utc(self) -> None:
        # A still-in-progress session must not surface in the
        # returning-user banner.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "sessions.sqlite"
            session_db.initialize(db_path)
            with session_db.connect(db_path) as conn:
                session_db.upsert_session(
                    conn,
                    session_id="still-running",
                    start_utc="2026-05-14T10:00:00+00:00",
                    end_utc=None,
                    goal="x",
                    case_name="anycase",
                    planner="openai",
                    model="gpt-5.5",
                    ok=None,
                )
                conn.commit()
            self.assertIsNone(welcome.lookup_last_session(db_path=db_path))


class FormatRelativeTimeTests(unittest.TestCase):
    """The relative-time formatter must be human-friendly."""

    def test_minutes(self) -> None:
        now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
        prior = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
        self.assertEqual(welcome.format_relative_time(prior, now=now), "30 minutes ago")

    def test_hours(self) -> None:
        now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
        prior = (now - timedelta(hours=2)).isoformat(timespec="seconds")
        self.assertEqual(welcome.format_relative_time(prior, now=now), "2 hours ago")

    def test_days(self) -> None:
        now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
        prior = (now - timedelta(days=3)).isoformat(timespec="seconds")
        self.assertEqual(welcome.format_relative_time(prior, now=now), "3 days ago")

    def test_just_now(self) -> None:
        now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
        prior = (now - timedelta(seconds=30)).isoformat(timespec="seconds")
        self.assertEqual(welcome.format_relative_time(prior, now=now), "just now")


class ReturningUserBannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._supports_color_patch = mock.patch.object(
            welcome.ui_colors, "supports_color", return_value=False
        )
        self._supports_color_patch.start()

    def tearDown(self) -> None:
        self._supports_color_patch.stop()

    def test_contains_last_session_reference(self) -> None:
        prior = {
            "session_id": "20260514_080000_tecnopolo_run",
            "case_name": "tecnopolo",
            "end_utc": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"),
            "ok": True,
        }
        banner = welcome.render_returning_banner(
            session_label="session-100000",
            profile_name="quick",
            last_session=prior,
        )
        self.assertIn("Last session", banner)
        self.assertIn("tecnopolo", banner)
        self.assertIn("hours ago", banner)

    def test_no_prior_session_when_db_empty(self) -> None:
        # Acceptance: returning user with empty SessionDB must not
        # crash and must surface a graceful "No prior session" line.
        banner = welcome.render_returning_banner(
            session_label="session-100000",
            profile_name="quick",
            last_session=None,
        )
        self.assertIn("No prior session", banner)

    def test_contains_version(self) -> None:
        banner = welcome.render_returning_banner(
            session_label="session-100000",
            profile_name="quick",
            last_session=None,
        )
        # Avoid hard-coding the version; just assert the header tag.
        self.assertIn("AISWMM", banner)

    def test_contains_tip_line(self) -> None:
        banner = welcome.render_returning_banner(
            session_label="session-100000",
            profile_name="quick",
            last_session=None,
        )
        for token in ("/help", "/exit", "/new-session", "--safe"):
            self.assertIn(token, banner)

    def test_fits_80_columns(self) -> None:
        prior = {
            "session_id": "20260514_080000_tecnopolo_run",
            "case_name": "tecnopolo",
            "end_utc": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"),
            "ok": True,
        }
        banner = welcome.render_returning_banner(
            session_label="session-100000",
            profile_name="quick",
            last_session=prior,
        )
        for line in banner.splitlines():
            self.assertLessEqual(
                len(_strip_ansi(line)),
                80,
                msg=f"Line exceeds 80 cols: {line!r}",
            )


class PrintWelcomeReturningTests(unittest.TestCase):
    """Marker present -> returning banner printed, with SessionDB hit."""

    def test_prints_short_banner_when_marker_exists(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            marker.write_text('{"first_run_at": "2026-05-01T00:00:00Z"}')
            db_path = Path(tmp) / "sessions.sqlite"
            prior = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
            _seed_session(
                db_path,
                session_id="20260514_080000_tecnopolo_run",
                case_name="tecnopolo",
                end_utc=prior,
            )
            buf = io.StringIO()
            with mock.patch.object(
                welcome, "first_run_marker_path", return_value=marker
            ), mock.patch.object(
                welcome.ui_colors, "supports_color", return_value=False
            ):
                welcome.print_welcome(
                    stream=buf,
                    session_label="session-100000",
                    profile_name="quick",
                    db_path=db_path,
                )
            output = buf.getvalue()
            self.assertNotIn("Welcome to AISWMM", output, output)
            self.assertIn("Last session", output)
            self.assertIn("tecnopolo", output)


if __name__ == "__main__":
    unittest.main()
