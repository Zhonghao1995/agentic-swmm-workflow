"""PRD-TUI-REDESIGN: non-TTY stdout strips colour but keeps chrome.

A redirected pipe or file (``aiswmm ... > log.txt``) should not see
ANSI escape codes — but it should still see the ``[ERR]`` prefix and
the frame box characters, because a downstream log scraper might
filter on those structures.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _NonTTYStream:
    """Stub stdout that mimics a pipe / file redirect."""

    def isatty(self) -> bool:
        return False


class TtyDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _NonTTYStream())
        self._stdout_patch.start()
        import os

        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)

    def tearDown(self) -> None:
        self._stdout_patch.stop()

    def test_use_colour_returns_false_on_non_tty(self) -> None:
        self.assertFalse(tui_chrome.use_colour())

    def test_use_chrome_returns_true_on_non_tty(self) -> None:
        # Chrome is only stripped by AISWMM_TUI=plain; a non-TTY pipe
        # still wants the prefixes for log scrapers.
        self.assertTrue(tui_chrome.use_chrome())

    def test_colour_helpers_strip_escapes_on_non_tty(self) -> None:
        self.assertEqual(tui_chrome.phosphor_green("x"), "x")
        self.assertEqual(tui_chrome.error_red("x"), "x")

    def test_prefix_builders_keep_prefix_on_non_tty(self) -> None:
        out = tui_chrome.sys("EXECUTING")
        self.assertIn("[SYS]", out)
        self.assertNotIn("\033", out)

    def test_frame_keeps_box_chars_on_non_tty(self) -> None:
        out = tui_chrome.frame("TITLE", ["line"])
        self.assertNotIn("\033", out)
        for ch in ("╭", "╮", "╰", "╯"):
            self.assertIn(ch, out)


if __name__ == "__main__":
    unittest.main()
