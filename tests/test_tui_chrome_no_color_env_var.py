"""PRD-TUI-REDESIGN: ``NO_COLOR=1`` strips colour, keeps chrome.

The no-color spec (https://no-color.org/) is explicitly about colour
escapes — frames and prefixes stay so a CI log can still scan for
``[ERR]`` markers without filtering ANSI sequences.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class NoColorEnvVarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()
        import os

        os.environ["AISWMM_TUI"] = "retro"
        os.environ["NO_COLOR"] = "1"

    def tearDown(self) -> None:
        self._stdout_patch.stop()
        import os

        os.environ.pop("NO_COLOR", None)

    def test_phosphor_green_returns_plain_text_under_no_color(self) -> None:
        out = tui_chrome.phosphor_green("hello")
        self.assertEqual(out, "hello")
        self.assertNotIn("\033", out)

    def test_sys_keeps_prefix_under_no_color(self) -> None:
        out = tui_chrome.sys("EXECUTING")
        # No colour codes...
        self.assertNotIn("\033", out)
        # ...but the [SYS] prefix is still there.
        self.assertIn("[SYS]", out)
        self.assertIn("EXECUTING", out)

    def test_err_keeps_prefix_under_no_color(self) -> None:
        out = tui_chrome.err("kaboom")
        self.assertNotIn("\033", out)
        self.assertIn("[ERR]", out)
        self.assertIn("kaboom", out)

    def test_frame_keeps_box_chars_under_no_color(self) -> None:
        out = tui_chrome.frame("RUN", ["line1"])
        self.assertNotIn("\033", out)
        # Box-drawing characters survive — only colour is stripped.
        for ch in ("╭", "╮", "╰", "╯", "─", "│"):
            self.assertIn(ch, out)


if __name__ == "__main__":
    unittest.main()
