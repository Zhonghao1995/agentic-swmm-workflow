"""PRD-TUI-REDESIGN: ``NO_COLOR=1`` AND ``AISWMM_TUI=plain`` combined.

When both are set, ``AISWMM_TUI=plain`` wins (strictest). The
combined output is byte-identical to ``AISWMM_TUI=plain`` alone.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class CombinedOptOutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()

    def tearDown(self) -> None:
        self._stdout_patch.stop()
        import os

        os.environ.pop("NO_COLOR", None)
        os.environ["AISWMM_TUI"] = "retro"

    def test_combined_matches_plain_alone(self) -> None:
        import os

        # Capture output under plain alone.
        os.environ["AISWMM_TUI"] = "plain"
        os.environ.pop("NO_COLOR", None)
        plain_only = [
            tui_chrome.sys("EXECUTING swmm_run"),
            tui_chrome.inf("COMPLETE"),
            tui_chrome.err("kaboom"),
            tui_chrome.wrn("careful"),
            tui_chrome.frame("RUN", ["alpha", "beta"]),
        ]

        # Capture output under both set.
        os.environ["AISWMM_TUI"] = "plain"
        os.environ["NO_COLOR"] = "1"
        combined = [
            tui_chrome.sys("EXECUTING swmm_run"),
            tui_chrome.inf("COMPLETE"),
            tui_chrome.err("kaboom"),
            tui_chrome.wrn("careful"),
            tui_chrome.frame("RUN", ["alpha", "beta"]),
        ]

        self.assertEqual(plain_only, combined)


if __name__ == "__main__":
    unittest.main()
