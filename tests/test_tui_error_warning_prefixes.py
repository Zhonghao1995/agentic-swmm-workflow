"""PRD-TUI-REDESIGN: ``[ERR]`` / ``[WRN]`` prefixes via ui.err / ui.wrn.

The canonical way to emit an error or warning anywhere in the agent
is ``ui.err(msg)`` / ``ui.wrn(msg)`` (build the string) or
``ui.say_err(msg)`` / ``ui.say_wrn(msg)`` (print to stderr). Both
honour ``AISWMM_TUI=plain`` automatically.
"""

from __future__ import annotations

import io
import os
import unittest
from unittest import mock

from agentic_swmm.agent import tui_chrome
from agentic_swmm.agent import ui


class _TTYStream:
    def isatty(self) -> bool:
        return True


class ErrorWarningPrefixesTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["AISWMM_TUI"] = "retro"
        os.environ.pop("NO_COLOR", None)
        self._stdout_patch = mock.patch.object(tui_chrome._sys, "stdout", _TTYStream())
        self._stdout_patch.start()

    def tearDown(self) -> None:
        self._stdout_patch.stop()

    def test_err_returns_red_prefixed_string(self) -> None:
        out = ui.err("binary missing")
        self.assertIn("[ERR] binary missing", out)
        self.assertIn(tui_chrome.ERROR_RED, out)

    def test_wrn_returns_amber_prefixed_string(self) -> None:
        out = ui.wrn("no active run dir")
        self.assertIn("[WRN] no active run dir", out)
        self.assertIn(tui_chrome.WARN_AMBER, out)

    def test_say_err_writes_to_stderr_stream(self) -> None:
        buf = io.StringIO()
        ui.say_err("kaboom", stream=buf)
        text = buf.getvalue()
        self.assertIn("[ERR] kaboom", text)
        self.assertTrue(text.endswith("\n"))

    def test_say_wrn_writes_to_stderr_stream(self) -> None:
        buf = io.StringIO()
        ui.say_wrn("careful", stream=buf)
        self.assertIn("[WRN] careful", buf.getvalue())

    def test_plain_mode_strips_err_wrn_prefix(self) -> None:
        os.environ["AISWMM_TUI"] = "plain"
        self.assertEqual(ui.err("x"), "x")
        self.assertEqual(ui.wrn("y"), "y")


if __name__ == "__main__":
    unittest.main()
