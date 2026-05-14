"""``NO_COLOR=1`` must strip every ANSI escape from the welcome output.

Issue #57 (UX-2) acceptance: no ANSI escape codes in output when
``NO_COLOR`` is set, on either the first-run path or the returning-user
path. Log scrapers and CI tooling rely on the no-color discipline.
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


class NoColorTests(unittest.TestCase):
    def test_extended_welcome_no_ansi_when_no_color(self) -> None:
        # ``ui_colors.supports_color`` returns False when NO_COLOR is
        # set, so we patch it as the single chokepoint.
        with mock.patch.object(welcome.ui_colors, "supports_color", return_value=False):
            text = welcome.render_extended_welcome()
        self.assertEqual(text, _ANSI_RE.sub("", text))
        self.assertNotIn("\x1b[", text)

    def test_logo_no_ansi_when_no_color(self) -> None:
        with mock.patch.object(welcome.ui_colors, "supports_color", return_value=False):
            logo = welcome.render_logo()
        self.assertNotIn("\x1b[", logo)

    def test_returning_banner_no_ansi_when_no_color(self) -> None:
        prior = {
            "session_id": "20260514_080000_tecnopolo_run",
            "case_name": "tecnopolo",
            "end_utc": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"),
            "ok": True,
        }
        with mock.patch.object(welcome.ui_colors, "supports_color", return_value=False):
            banner = welcome.render_returning_banner(
                session_label="session-100000",
                profile_name="quick",
                last_session=prior,
            )
        self.assertEqual(banner, _ANSI_RE.sub("", banner))
        self.assertNotIn("\x1b[", banner)

    def test_print_welcome_no_ansi_on_first_run(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            buf = io.StringIO()
            with mock.patch.object(
                welcome, "first_run_marker_path", return_value=marker
            ), mock.patch.object(
                welcome.ui_colors, "supports_color", return_value=False
            ):
                welcome.print_welcome(stream=buf)
            self.assertNotIn("\x1b[", buf.getvalue())

    def test_print_welcome_no_ansi_on_returning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            marker.write_text('{"first_run_at": "2026-05-01T00:00:00Z"}')
            db_path = Path(tmp) / "sessions.sqlite"
            session_db.initialize(db_path)
            with session_db.connect(db_path) as conn:
                session_db.upsert_session(
                    conn,
                    session_id="20260514_080000_tecnopolo_run",
                    start_utc="2026-05-14T08:00:00+00:00",
                    end_utc=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds"),
                    goal="run the demo",
                    case_name="tecnopolo",
                    planner="openai",
                    model="gpt-5.5",
                    ok=True,
                )
                conn.commit()
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
            self.assertNotIn("\x1b[", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
