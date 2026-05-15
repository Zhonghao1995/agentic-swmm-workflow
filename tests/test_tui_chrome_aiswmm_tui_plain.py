"""PRD-TUI-REDESIGN: ``AISWMM_TUI=plain`` strips everything.

Plain mode is the aiswmm-specific opt-out for paper screenshots: no
ANSI escapes, no ``[SYS]``-style prefixes, no rounded-frame box-
drawing characters. Pure ASCII output suitable for copy-paste.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class AiswmmTuiPlainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()
        import os

        os.environ["AISWMM_TUI"] = "plain"
        os.environ.pop("NO_COLOR", None)

    def tearDown(self) -> None:
        self._stdout_patch.stop()
        import os

        os.environ["AISWMM_TUI"] = "retro"

    def test_is_plain_returns_true(self) -> None:
        self.assertTrue(tui_chrome.is_plain())

    def test_use_colour_returns_false(self) -> None:
        self.assertFalse(tui_chrome.use_colour())

    def test_use_chrome_returns_false(self) -> None:
        self.assertFalse(tui_chrome.use_chrome())

    def test_colour_helpers_emit_plain_text(self) -> None:
        for fn in (
            tui_chrome.phosphor_green,
            tui_chrome.phosphor_dim,
            tui_chrome.warn_amber,
            tui_chrome.error_red,
        ):
            self.assertEqual(fn("hello"), "hello")

    def test_prefix_builders_strip_prefix_and_colour(self) -> None:
        self.assertEqual(tui_chrome.sys("EXECUTING"), "EXECUTING")
        self.assertEqual(tui_chrome.inf("COMPLETE"), "COMPLETE")
        self.assertEqual(tui_chrome.err("kaboom"), "kaboom")
        self.assertEqual(tui_chrome.wrn("careful"), "careful")

    def test_frame_contains_no_box_drawing_or_colour(self) -> None:
        out = tui_chrome.frame("TITLE", ["alpha", "beta"])
        for ch in ("╭", "╮", "╰", "╯", "─", "│", "\033"):
            self.assertNotIn(ch, out)


if __name__ == "__main__":
    unittest.main()
