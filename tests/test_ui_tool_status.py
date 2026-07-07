"""Tests for the live tool-status seam in ``agentic_swmm.agent.ui``.

The executor registers its per-tool RUNNING spinner; long-blocking
handlers repaint the status line through ``update_tool_status`` without
holding a spinner reference. Outside an agent run the seam is a silent
no-op, and identical consecutive updates are deduped so non-TTY streams
(one line per update) are not spammed by poll ticks.
"""
from __future__ import annotations

import io
import unittest

from agentic_swmm.agent.ui import (
    Spinner,
    SpinnerState,
    set_active_tool_spinner,
    update_tool_status,
)


class ToolStatusSeamTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_active_tool_spinner(None)

    def test_noop_without_active_spinner(self) -> None:
        set_active_tool_spinner(None)
        update_tool_status("fetch_swmm_from_canada — BUILDING 40%")  # must not raise

    def test_updates_active_spinner_label(self) -> None:
        stream = io.StringIO()
        spinner = Spinner("fetch_swmm_from_canada", stream=stream, state=SpinnerState.RUNNING)
        set_active_tool_spinner(spinner)
        update_tool_status("fetch_swmm_from_canada — BUILDING 40%")
        self.assertEqual(spinner.label, "fetch_swmm_from_canada — BUILDING 40%")
        self.assertIn("BUILDING 40%", stream.getvalue())

    def test_identical_updates_are_deduped(self) -> None:
        stream = io.StringIO()
        spinner = Spinner("tool", stream=stream, state=SpinnerState.RUNNING)
        set_active_tool_spinner(spinner)
        update_tool_status("same text")
        update_tool_status("same text")
        update_tool_status("same text")
        # Non-TTY renders one line per real update — dedupe keeps it to one.
        self.assertEqual(stream.getvalue().count("same text"), 1)

    def test_reregistering_resets_dedupe(self) -> None:
        stream = io.StringIO()
        spinner = Spinner("tool", stream=stream, state=SpinnerState.RUNNING)
        set_active_tool_spinner(spinner)
        update_tool_status("stage A")
        set_active_tool_spinner(spinner)  # next tool announced
        update_tool_status("stage A")
        self.assertEqual(stream.getvalue().count("stage A"), 2)


if __name__ == "__main__":
    unittest.main()
