"""Unit tests for ``ui_colors``.

PRD_runtime: stdlib ANSI helpers, degrade to plain text on ``NO_COLOR``
or non-tty.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from agentic_swmm.agent import ui_colors


class SupportsColorTests(unittest.TestCase):
    def test_no_color_env_disables(self) -> None:
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            with mock.patch("sys.stdout") as fake_stdout:
                fake_stdout.isatty.return_value = True
                self.assertFalse(ui_colors.supports_color())

    def test_non_tty_disables(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdout") as fake_stdout:
                fake_stdout.isatty.return_value = False
                self.assertFalse(ui_colors.supports_color())

    def test_tty_without_no_color_enables(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "NO_COLOR"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("sys.stdout") as fake_stdout:
                fake_stdout.isatty.return_value = True
                self.assertTrue(ui_colors.supports_color())


class ColorizeTests(unittest.TestCase):
    def test_returns_plain_text_when_color_disabled(self) -> None:
        with mock.patch.object(ui_colors, "supports_color", return_value=False):
            self.assertEqual(
                ui_colors.colorize("hello", ui_colors.FG_GREEN),
                "hello",
            )

    def test_wraps_in_escapes_when_color_enabled(self) -> None:
        with mock.patch.object(ui_colors, "supports_color", return_value=True):
            wrapped = ui_colors.colorize("hello", ui_colors.FG_GREEN)
            self.assertTrue(wrapped.startswith(ui_colors.FG_GREEN))
            self.assertTrue(wrapped.endswith(ui_colors.RESET))
            self.assertIn("hello", wrapped)

    def test_empty_text_returns_empty(self) -> None:
        with mock.patch.object(ui_colors, "supports_color", return_value=True):
            self.assertEqual(ui_colors.colorize("", ui_colors.FG_GREEN), "")


if __name__ == "__main__":
    unittest.main()
