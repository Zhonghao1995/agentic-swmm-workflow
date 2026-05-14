"""``AISWMM_DISABLE_WELCOME=1`` must suppress the welcome entirely.

Issue #57 (UX-2) acceptance: when the env var is set the agent boots
directly to the prompt with no banner / welcome / logo printed. This
is what scripted / CI invocations rely on so log output stays scoped
to the actual run.
"""

from __future__ import annotations

import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import welcome


class DisabledEnvTests(unittest.TestCase):
    def test_disabled_env_suppresses_first_run(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            buf = io.StringIO()
            with mock.patch.object(
                welcome, "first_run_marker_path", return_value=marker
            ), mock.patch.dict(
                "os.environ", {"AISWMM_DISABLE_WELCOME": "1"}, clear=False
            ):
                welcome.print_welcome(stream=buf)
            self.assertEqual(buf.getvalue(), "")
            # The marker must not be written either — the user has
            # opted out, we don't want to silently consume "first
            # run" on their behalf.
            self.assertFalse(marker.exists())

    def test_disabled_env_suppresses_returning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            marker.write_text('{"first_run_at": "2026-05-01T00:00:00Z"}')
            buf = io.StringIO()
            with mock.patch.object(
                welcome, "first_run_marker_path", return_value=marker
            ), mock.patch.dict(
                "os.environ", {"AISWMM_DISABLE_WELCOME": "1"}, clear=False
            ):
                welcome.print_welcome(
                    stream=buf,
                    session_label="session-100000",
                    profile_name="quick",
                )
            self.assertEqual(buf.getvalue(), "")

    def test_disabled_env_is_explicit_value(self) -> None:
        # Only "1" or another truthy value should disable; an empty
        # or unset variable must let the welcome print normally.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            buf = io.StringIO()
            env_without = {
                k: v
                for k, v in __import__("os").environ.items()
                if k != "AISWMM_DISABLE_WELCOME"
            }
            with mock.patch.object(
                welcome, "first_run_marker_path", return_value=marker
            ), mock.patch.dict(
                "os.environ", env_without, clear=True
            ), mock.patch.object(
                welcome.ui_colors, "supports_color", return_value=False
            ):
                welcome.print_welcome(stream=buf)
            # Without the env var, first-run welcome should fire.
            self.assertIn("Welcome to AISWMM", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
