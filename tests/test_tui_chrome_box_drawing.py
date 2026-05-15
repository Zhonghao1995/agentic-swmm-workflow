"""PRD-TUI-REDESIGN: rounded-corner frame builder.

``tui_chrome.frame(title, lines)`` produces a Light-edge + Rounded-corner
box around the lines. Width auto-fits the longer of title or any line.
Plain mode strips the box entirely.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class FrameBoxDrawingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()
        import os

        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)

    def tearDown(self) -> None:
        self._stdout_patch.stop()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re

        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_frame_starts_with_top_left_corner_and_title(self) -> None:
        out = self._strip_ansi(tui_chrome.frame("TITLE", ["line1", "line2"]))
        self.assertTrue(out.startswith("╭─ TITLE"), out)

    def test_frame_has_top_right_corner(self) -> None:
        out = self._strip_ansi(tui_chrome.frame("T", ["x"]))
        first_line = out.splitlines()[0]
        self.assertTrue(first_line.endswith("╮"), first_line)

    def test_frame_has_rounded_bottom_corners(self) -> None:
        out = self._strip_ansi(tui_chrome.frame("T", ["x"]))
        last_line = out.splitlines()[-1]
        self.assertTrue(last_line.startswith("╰"), last_line)
        self.assertTrue(last_line.endswith("╯"), last_line)

    def test_frame_uses_light_edges(self) -> None:
        out = self._strip_ansi(tui_chrome.frame("T", ["body"]))
        # Body row starts with a light vertical bar.
        body_row = out.splitlines()[1]
        self.assertTrue(body_row.startswith("│"), body_row)
        self.assertTrue(body_row.endswith("│"), body_row)

    def test_frame_width_fits_longest_line(self) -> None:
        long_line = "this is a much longer body line than the title"
        out = self._strip_ansi(tui_chrome.frame("T", [long_line]))
        rows = out.splitlines()
        # All rows must be the same visible width.
        widths = {len(row) for row in rows}
        self.assertEqual(len(widths), 1, f"frame rows have uneven widths: {widths}")
        # And that width must be at least the length of the longest line
        # plus the two vertical edges and one space of padding.
        self.assertGreaterEqual(widths.pop(), len(long_line) + 2)

    def test_plain_mode_strips_frame_characters(self) -> None:
        import os

        os.environ["AISWMM_TUI"] = "plain"
        out = tui_chrome.frame("TITLE", ["alpha", "beta"])
        for ch in ("╭", "╮", "╰", "╯", "─", "│"):
            self.assertNotIn(ch, out)
        # Plain mode keeps the literal title text so screen readers
        # still hear "TITLE" announced.
        self.assertIn("TITLE", out)
        self.assertIn("alpha", out)
        self.assertIn("beta", out)


if __name__ == "__main__":
    unittest.main()
