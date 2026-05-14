"""First-run welcome: extended logo + capability tour.

Issue #57 (UX-2). When ``~/.aiswmm/first_run.json`` is absent the
welcome must print:

- An ASCII-art ``AISWMM`` logo (multi-line, <= 8 lines, <= 80 cols).
- A "Welcome to AISWMM" greeting + capability bullet list.
- A "Things to try" bullet list with at least one demo prompt.
- A trust line about "verified vs. uncertain".
- A tip line + closing call-to-action.

The marker file is then written so subsequent launches take the short
returning-user path.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import welcome


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


class FirstRunMarkerTests(unittest.TestCase):
    def test_marker_path_under_aiswmm_config(self) -> None:
        # The marker lives under the resolved aiswmm config dir so
        # the user's ``~/.aiswmm`` (or AISWMM_CONFIG_DIR override) is
        # the single source of truth for runtime state.
        path = welcome.first_run_marker_path()
        self.assertEqual(path.name, "first_run.json")
        # The parent dir is the aiswmm config dir (``.aiswmm`` leaf).
        self.assertEqual(path.parent.name, ".aiswmm")

    def test_is_first_run_true_when_marker_absent(self) -> None:
        with mock.patch.object(
            welcome, "first_run_marker_path", return_value=Path("/nonexistent/first_run.json")
        ):
            self.assertTrue(welcome.is_first_run())

    def test_is_first_run_false_when_marker_present(self, tmp_path=None) -> None:
        # Cannot use tmp_path here directly (unittest); fall back to a
        # local helper to keep this test self-contained.
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            marker.write_text(json.dumps({"installed_at": "2026-05-14T00:00:00Z"}))
            with mock.patch.object(welcome, "first_run_marker_path", return_value=marker):
                self.assertFalse(welcome.is_first_run())

    def test_mark_first_run_complete_writes_marker(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            with mock.patch.object(welcome, "first_run_marker_path", return_value=marker):
                welcome.mark_first_run_complete()
            self.assertTrue(marker.exists())
            payload = json.loads(marker.read_text(encoding="utf-8"))
            # The payload must record when the welcome was first shown
            # so we can debug "user says they never saw the welcome".
            self.assertIn("first_run_at", payload)


class ExtendedWelcomeRenderTests(unittest.TestCase):
    """The first-run welcome must include the logo + capability tour."""

    def setUp(self) -> None:
        self._supports_color_patch = mock.patch.object(
            welcome.ui_colors, "supports_color", return_value=False
        )
        self._supports_color_patch.start()

    def tearDown(self) -> None:
        self._supports_color_patch.stop()

    def test_includes_logo(self) -> None:
        text = welcome.render_extended_welcome()
        self.assertIn("AISWMM", text)
        # A multi-line logo: at least 4 lines of ASCII art before the
        # blank line that separates the logo from the greeting.
        first_paragraph = text.split("\n\n", 1)[0]
        self.assertGreaterEqual(len(first_paragraph.splitlines()), 4)

    def test_logo_fits_80_columns(self) -> None:
        # macOS Terminal default is 80 cols; the logo must not auto-wrap.
        text = welcome.render_extended_welcome()
        for line in text.splitlines():
            self.assertLessEqual(
                len(_strip_ansi(line)),
                80,
                msg=f"Line exceeds 80 cols: {line!r}",
            )

    def test_logo_at_most_8_lines_tall(self) -> None:
        # PRD constraint: <= 8 lines tall.
        logo = welcome.render_logo()
        lines = [ln for ln in logo.splitlines() if ln.strip()]
        self.assertLessEqual(len(lines), 8, msg=f"Logo too tall: {len(lines)} lines")

    def test_includes_welcome_greeting(self) -> None:
        text = welcome.render_extended_welcome()
        self.assertIn("Welcome to AISWMM", text)

    def test_includes_capability_bullets(self) -> None:
        text = welcome.render_extended_welcome()
        # The PRD lists five concrete capabilities. We assert on key
        # nouns that must appear so the wording is locked but the
        # exact phrasing has wiggle room.
        for token in ("SWMM", "Calibrate", "uncertainty", "lessons"):
            self.assertIn(token, text)

    def test_includes_things_to_try(self) -> None:
        text = welcome.render_extended_welcome()
        self.assertIn("Things to try", text)
        # At least one of the demo prompts from the PRD.
        self.assertIn("tecnopolo", text.lower())

    def test_includes_trust_line(self) -> None:
        # "I'll always tell you what I've actually verified vs. what's
        # still uncertain." — the trust contract is load-bearing for
        # this product and must not be silently dropped.
        text = welcome.render_extended_welcome()
        self.assertIn("verified", text)
        self.assertIn("uncertain", text)

    def test_includes_tip_line(self) -> None:
        text = welcome.render_extended_welcome()
        self.assertIn("/help", text)


class PrintFirstRunWelcomeTests(unittest.TestCase):
    """End-to-end: marker absent -> extended welcome printed + marker written."""

    def test_prints_welcome_and_writes_marker(self) -> None:
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "first_run.json"
            # Capture stdout to verify the welcome was actually printed.
            buf = io.StringIO()
            with mock.patch.object(
                welcome, "first_run_marker_path", return_value=marker
            ), mock.patch.object(
                welcome.ui_colors, "supports_color", return_value=False
            ):
                welcome.print_welcome(stream=buf)
            output = buf.getvalue()
            self.assertIn("Welcome to AISWMM", output)
            self.assertIn("AISWMM", output)
            # Marker must be written so the next launch takes the
            # returning-user path.
            self.assertTrue(marker.exists())


if __name__ == "__main__":
    unittest.main()
