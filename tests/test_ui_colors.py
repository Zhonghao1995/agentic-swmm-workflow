"""Unit tests for ``ui_colors``.

PRD_runtime: stdlib ANSI helpers, degrade to plain text on ``NO_COLOR``
or non-tty.
"""
from __future__ import annotations

import inspect
import os
import unittest
from unittest import mock

from agentic_swmm.agent import ui as ui_module
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


class ClearLineConstantTests(unittest.TestCase):
    """Issue #190: the CR + erase-in-line escape sequence used by the
    spinner to wipe its residual frame must live in ``ui_colors`` so
    every ANSI escape funnels through the central module — same
    convention as ``RESET`` / ``DIM`` / ``BOLD``.
    """

    def test_clear_line_constant_value(self) -> None:
        # CR + CSI 2 K = move cursor to column 0, erase entire line.
        self.assertEqual(ui_colors.CLEAR_LINE, "\r\033[2K")

    def test_ui_module_has_no_inline_ansi_escape(self) -> None:
        """``agentic_swmm/agent/ui.py`` must not concatenate raw ANSI
        escapes by hand — every escape funnels through ``ui_colors``
        / ``tui_chrome`` per project convention.
        """
        source = inspect.getsource(ui_module)
        self.assertNotIn(
            "\\x1b",
            source,
            "ui.py must reference ANSI escapes via ui_colors constants, "
            "not inline \\x1b literals",
        )
        self.assertNotIn(
            "\\033",
            source,
            "ui.py must reference ANSI escapes via ui_colors constants, "
            "not inline \\033 literals",
        )


class SpinnerFinishCommentHygieneTests(unittest.TestCase):
    """Issue #190: the in-line comment above ``Spinner.finish``'s
    CR + erase-line write must be brief and carry only the non-obvious
    WHY — no narrative re-explanation, no issue-number prefix. (PR #188
    landed a 7-line comment prefixed with ``# Issue #184``; we trim it
    down here.)
    """

    def _finish_clear_line_comment(self) -> list[str]:
        source = inspect.getsource(ui_module.Spinner.finish)
        lines = source.splitlines()
        # Walk backwards from the ``self.stream.write(...CLEAR_LINE)``
        # line: skip over the ``try:`` scaffolding, then collect the
        # contiguous block of ``#``-prefixed comment lines that
        # describe the write.
        write_idx = next(
            i for i, line in enumerate(lines) if "CLEAR_LINE" in line
        )
        i = write_idx - 1
        # Skip blank / non-comment scaffold lines (e.g. ``try:``).
        while i >= 0 and not lines[i].lstrip().startswith("#"):
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("try"):
                break
            i -= 1
        comment: list[str] = []
        while i >= 0 and lines[i].lstrip().startswith("#"):
            comment.append(lines[i].lstrip()[1:].strip())
            i -= 1
        comment.reverse()
        return comment

    def test_finish_comment_no_more_than_three_lines(self) -> None:
        comment = self._finish_clear_line_comment()
        self.assertLessEqual(
            len(comment),
            3,
            f"Spinner.finish CR + erase-line comment must be <= 3 lines; "
            f"got {len(comment)}:\n" + "\n".join(comment),
        )

    def test_finish_comment_has_no_issue_number_prefix(self) -> None:
        comment = self._finish_clear_line_comment()
        self.assertTrue(comment, "expected a comment above the CLEAR_LINE write")
        first_line = comment[0]
        self.assertFalse(
            first_line.lower().startswith("issue #"),
            f"Spinner.finish comment must not lead with an ``Issue #N`` "
            f"prefix; got {first_line!r}",
        )


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
