"""PRD-TUI-REDESIGN: ``[SYS]`` / ``[INF]`` / ``[ERR]`` / ``[WRN]`` builders.

Retro mode wraps the prefix + message in the appropriate colour.
Plain mode strips both the prefix and the colour — that's the strict
opt-out for paper screenshots.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome


class _TTYStream:
    def isatty(self) -> bool:
        return True


class PrefixBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()
        import os

        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)

    def tearDown(self) -> None:
        self._stdout_patch.stop()

    def test_sys_emits_phosphor_green_prefixed_text(self) -> None:
        out = tui_chrome.sys("EXECUTING")
        self.assertIn("[SYS] EXECUTING", out)
        self.assertIn(tui_chrome.PHOSPHOR_GREEN, out)
        self.assertTrue(out.endswith(tui_chrome.RESET))

    def test_inf_emits_phosphor_green(self) -> None:
        out = tui_chrome.inf("COMPLETE")
        self.assertIn("[INF] COMPLETE", out)
        self.assertIn(tui_chrome.PHOSPHOR_GREEN, out)

    def test_err_emits_red_prefix(self) -> None:
        out = tui_chrome.err("kaboom")
        self.assertIn("[ERR] kaboom", out)
        self.assertIn(tui_chrome.ERROR_RED, out)

    def test_wrn_emits_amber_prefix(self) -> None:
        out = tui_chrome.wrn("careful")
        self.assertIn("[WRN] careful", out)
        self.assertIn(tui_chrome.WARN_AMBER, out)

    def test_plain_mode_strips_prefix_and_colour(self) -> None:
        import os

        os.environ["AISWMM_TUI"] = "plain"
        for fn, msg in (
            (tui_chrome.sys, "EXECUTING"),
            (tui_chrome.inf, "COMPLETE"),
            (tui_chrome.err, "kaboom"),
            (tui_chrome.wrn, "careful"),
        ):
            out = fn(msg)
            self.assertEqual(out, msg, f"{fn.__name__} should strip prefix in plain mode")
            self.assertNotIn("[", out)
            self.assertNotIn("\033", out)


if __name__ == "__main__":
    unittest.main()
