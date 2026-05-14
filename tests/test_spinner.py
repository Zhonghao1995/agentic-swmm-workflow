"""Tests for the ``Spinner`` class.

PRD_runtime "Module: Spinner": ``\\r`` overwrite on TTY, one
newline-terminated line per update on non-TTY.
"""
from __future__ import annotations

import io
import unittest

from agentic_swmm.agent.ui import Spinner


class _FakeTTYStream(io.StringIO):
    """StringIO subclass that pretends to be a TTY."""

    def isatty(self) -> bool:  # type: ignore[override]
        return True


class SpinnerTests(unittest.TestCase):
    def test_spinner_writes_carriage_return_on_tty(self) -> None:
        stream = _FakeTTYStream()
        with Spinner("doctor", stream=stream) as spinner:
            spinner.update("read_file")
            spinner.update("plot_run")
        output = stream.getvalue()
        self.assertIn("\r", output, "spinner must use carriage return on tty")
        self.assertIn("doctor", output)
        self.assertIn("read_file", output)
        self.assertIn("plot_run", output)

    def test_spinner_falls_back_to_newlines_on_non_tty(self) -> None:
        stream = io.StringIO()  # isatty() returns False on plain StringIO
        with Spinner("doctor", stream=stream) as spinner:
            spinner.update("read_file")
        output = stream.getvalue()
        self.assertNotIn("\r", output, "no carriage return on non-tty")
        # One line per update (doctor + read_file).
        self.assertEqual(output.count("\n"), 2)


if __name__ == "__main__":
    unittest.main()
