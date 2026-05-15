"""PRD-TUI-REDESIGN: phosphor-green palette functions.

Each colour helper emits the correct 256-colour ANSI escape sequence
under retro mode and returns the bare text under plain mode. These
are the lowest-level chrome primitives; everything else (prefixes,
frames) composes them.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _TTYStream:
    """Stub stdout that pretends to be a terminal."""

    def isatty(self) -> bool:
        return True


class PhosphorPaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        # Force a TTY so use_colour() returns True under retro mode.
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()
        # Clean env so each test sees a deterministic starting state.
        self._env_patch = mock.patch.dict(
            "os.environ", {"AISWMM_TUI": "retro"}, clear=False
        )
        self._env_patch.start()
        # Remove NO_COLOR if it leaks in from the parent shell.
        import os

        os.environ.pop("NO_COLOR", None)

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._stdout_patch.stop()

    def test_phosphor_green_wraps_text_in_escape_and_reset(self) -> None:
        result = tui_chrome.phosphor_green("hello")
        self.assertEqual(result, "\033[38;5;46mhello\033[0m")

    def test_phosphor_dim_uses_dimmer_256_colour(self) -> None:
        result = tui_chrome.phosphor_dim("dim")
        self.assertIn("\033[38;5;28m", result)
        self.assertTrue(result.endswith("\033[0m"))

    def test_warn_amber_uses_amber_256_colour(self) -> None:
        result = tui_chrome.warn_amber("warn")
        self.assertIn("\033[38;5;214m", result)

    def test_error_red_uses_red_256_colour(self) -> None:
        result = tui_chrome.error_red("err")
        self.assertIn("\033[38;5;196m", result)

    def test_plain_mode_strips_colour(self) -> None:
        import os

        os.environ["AISWMM_TUI"] = "plain"
        # phosphor_green returns plain text — no escape codes at all.
        result = tui_chrome.phosphor_green("hello")
        self.assertEqual(result, "hello")
        self.assertNotIn("\033", result)


if __name__ == "__main__":
    unittest.main()
