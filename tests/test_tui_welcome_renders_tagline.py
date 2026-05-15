"""PRD-TUI-REDESIGN: welcome screen renders the ``[SYS] aiswmm`` tagline.

The PR #72 ASCII logo and the new rounded-frame tagline must both
appear in the first-run welcome output. Plain mode collapses the
tagline to the literal ``aiswmm vX.Y.Z ONLINE`` text without box
characters; we test the retro path here.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from agentic_swmm import __version__
from agentic_swmm.agent import tui_chrome
from agentic_swmm.agent import welcome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class WelcomeRendersTaglineTests(unittest.TestCase):
    def setUp(self) -> None:
        # Force chrome on for this test; the test asserts retro output.
        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()

    def tearDown(self) -> None:
        self._stdout_patch.stop()

    @staticmethod
    def _strip_ansi(text: str) -> str:
        import re

        return re.sub(r"\x1b\[[0-9;]*m", "", text)

    def test_extended_welcome_contains_logo_and_tagline_frame(self) -> None:
        text = welcome.render_extended_welcome()
        stripped = self._strip_ansi(text)

        # The PR #72 ASCII logo must still be there.
        self.assertIn("AISWMM", stripped)

        # The new retro tagline lives inside a rounded frame.
        self.assertIn(f"[SYS] aiswmm v{__version__} ONLINE", stripped)
        self.assertIn("I'm aiswmm.", stripped)

        # And the frame uses rounded corners.
        for ch in ("╭", "╮", "╰", "╯"):
            self.assertIn(ch, stripped)

    def test_tagline_frame_renders_independently(self) -> None:
        # The render_tagline_frame helper is a public surface so any
        # caller (not just render_extended_welcome) can opt in.
        tagline = welcome.render_tagline_frame()
        stripped = self._strip_ansi(tagline)
        self.assertIn(f"[SYS] aiswmm v{__version__} ONLINE", stripped)


if __name__ == "__main__":
    unittest.main()
