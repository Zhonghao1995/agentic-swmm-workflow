"""PRD-TUI-REDESIGN: end-to-end plain-mode parity check.

Under ``AISWMM_TUI=plain`` the full surface area of chrome must
disappear: no ANSI escapes, no box-drawing, no ``[SYS]/[INF]/[ERR]/[WRN]``
prefixes. This test exercises every chrome producer in one go so a
regression anywhere shows up here.
"""

from __future__ import annotations

import io
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from agentic_swmm.agent import reporting
from agentic_swmm.agent import runtime_loop
from agentic_swmm.agent import tui_chrome
from agentic_swmm.agent import ui
from agentic_swmm.agent import welcome


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_CHROME_CHARS = ("╭", "╮", "╰", "╯", "─", "│")
_PREFIX_TOKENS = ("[SYS]", "[INF]", "[ERR]", "[WRN]")


class _StubExecutor:
    def execute(self, call, *, index=None):
        return {"tool": call.name, "ok": True, "summary": "ran"}


class PlainModeStripsChromeEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["AISWMM_TUI"] = "plain"
        os.environ.pop("NO_COLOR", None)

    def tearDown(self) -> None:
        os.environ["AISWMM_TUI"] = "retro"

    def _assert_no_chrome(self, text: str, label: str) -> None:
        self.assertNotRegex(text, _ANSI_RE, f"{label}: ANSI escape found")
        for ch in _CHROME_CHARS:
            self.assertNotIn(ch, text, f"{label}: box-drawing char {ch!r} present")
        for tok in _PREFIX_TOKENS:
            self.assertNotIn(tok, text, f"{label}: prefix token {tok!r} present")

    def test_welcome_tagline_strips_to_pure_ascii(self) -> None:
        self._assert_no_chrome(welcome.render_tagline_frame(), "tagline_frame")

    def test_extended_welcome_strips_to_pure_ascii(self) -> None:
        self._assert_no_chrome(welcome.render_extended_welcome(), "extended_welcome")

    def test_tool_banners_strip_to_pure_ascii(self) -> None:
        buf = io.StringIO()
        executor = _StubExecutor()
        call = SimpleNamespace(name="swmm_run", args={})
        runtime_loop.execute_with_chrome(executor, call, stream=buf)
        self._assert_no_chrome(buf.getvalue(), "tool_banners")

    def test_err_wrn_strip_to_pure_ascii(self) -> None:
        self._assert_no_chrome(ui.err("kaboom"), "err()")
        self._assert_no_chrome(ui.wrn("careful"), "wrn()")

    def test_result_card_strips_to_pure_ascii(self) -> None:
        card = reporting.render_result_card_from_run(
            session_dir=Path("runs/x"),
            results=[{"ok": True, "tool": "build_inp"}],
            dry_run=False,
        )
        self._assert_no_chrome(card, "result_card")


if __name__ == "__main__":
    unittest.main()
