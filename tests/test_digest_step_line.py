"""PRD-185: single-line step renderer + failure auto-expansion.

The PRD pins four rendered shapes (one per permission/outcome combo):

| Decision           | Result   | Line                                                    |
|--------------------|----------|---------------------------------------------------------|
| Prompted (write)   | Success  | [NN] tool  -> [Y/n]: Y  ok <brief>                      |
| Prompted (write)   | Failure  | [NN] tool  -> [Y/n]: Y  x <reason>                      |
| Auto (read-only)   | Success  | [NN] tool (read-only, auto)  ok <brief>                 |
| Auto (read-only)   | Failure  | [NN] tool (read-only, auto)  x <reason>                 |
| Denied             | -        | [NN] tool  -> [Y/n]: N  (skipped)                       |

Plus: on failure the full error message is indented two spaces and
rendered on the lines immediately below, without ``--verbose``.

This test file pins the pure renderer in ``digest_render.render_step``.
The check / x markers are intentionally rendered with the unicode
characters required by the PRD; ASCII fallback is OUT OF SCOPE here
(the existing UI ships ANSI/unicode in retro-chrome already).
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.digest_render import render_step


# Unicode marks pinned by the PRD. Importing the symbols by name from
# the test keeps the assertion message readable when it fails.
_OK_MARK = "✓"  # ✓
_FAIL_MARK = "✗"  # ✗


class StepLineRenderTests(unittest.TestCase):
    def test_auto_approved_read_only_success(self) -> None:
        line = render_step(
            step=18,
            tool="select_skill",
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            brief="swmm-experiment-audit",
            error_detail=None,
        )
        self.assertEqual(
            line,
            f"[18] select_skill (read-only, auto)  {_OK_MARK} swmm-experiment-audit",
        )

    def test_auto_approved_read_only_failure_with_detail(self) -> None:
        line = render_step(
            step=20,
            tool="inspect_plot_options",
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=False,
            brief="out_file not in repo",
            error_detail=(
                "out_file must be an existing repository file:\n"
                "  /Users/.../runs/2026-05-22/230510_tecnopolo_run/plot_options.json"
            ),
        )
        self.assertEqual(
            line,
            "\n".join(
                [
                    f"[20] inspect_plot_options (read-only, auto)  "
                    f"{_FAIL_MARK} out_file not in repo",
                    "    Detail: out_file must be an existing repository file:",
                    "      /Users/.../runs/2026-05-22/230510_tecnopolo_run/plot_options.json",
                ]
            ),
        )

    def test_prompted_write_success_renders_inline_yn(self) -> None:
        line = render_step(
            step=17,
            tool="run_swmm_inp",
            is_read_only=False,
            prompted=True,
            approved=True,
            ok=True,
            brief="saanich-1779596754",
            error_detail=None,
        )
        self.assertEqual(
            line,
            f"[17] run_swmm_inp  -> [Y/n]: Y  {_OK_MARK} saanich-1779596754",
        )

    def test_prompted_write_failure_renders_reason(self) -> None:
        line = render_step(
            step=17,
            tool="run_swmm_inp",
            is_read_only=False,
            prompted=True,
            approved=True,
            ok=False,
            brief="swmm5 missing",
            error_detail="swmm5 binary not on PATH",
        )
        self.assertEqual(
            line,
            "\n".join(
                [
                    f"[17] run_swmm_inp  -> [Y/n]: Y  {_FAIL_MARK} swmm5 missing",
                    "    Detail: swmm5 binary not on PATH",
                ]
            ),
        )

    def test_denied_prompted_renders_skipped(self) -> None:
        line = render_step(
            step=19,
            tool="apply_patch",
            is_read_only=False,
            prompted=True,
            approved=False,
            ok=False,
            brief="tool not approved by user",
            error_detail=None,
        )
        self.assertEqual(
            line,
            "[19] apply_patch  -> [Y/n]: N  (skipped)",
        )

    def test_success_with_empty_brief_omits_trailing_space(self) -> None:
        # When no meaningful brief is available the row ends right
        # after the check mark — no dangling space, no placeholder.
        line = render_step(
            step=22,
            tool="weird_tool",
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=True,
            brief="",
            error_detail=None,
        )
        self.assertEqual(line, f"[22] weird_tool (read-only, auto)  {_OK_MARK}")

    def test_failure_without_detail_renders_only_top_line(self) -> None:
        # The auto-expand only fires when a detail string is supplied.
        # Pure marker + brief, no Detail: indent, no trailing newline.
        line = render_step(
            step=5,
            tool="list_dir",
            is_read_only=True,
            prompted=False,
            approved=True,
            ok=False,
            brief="path does not exist",
            error_detail=None,
        )
        self.assertEqual(
            line,
            f"[5] list_dir (read-only, auto)  {_FAIL_MARK} path does not exist",
        )


class FailureDetailIndentTests(unittest.TestCase):
    """Auto-expansion indentation contract (no --verbose required)."""

    def test_multiline_detail_indents_continuation_lines(self) -> None:
        line = render_step(
            step=11,
            tool="run_swmm_inp",
            is_read_only=False,
            prompted=True,
            approved=True,
            ok=False,
            brief="swmm exited 1",
            error_detail="Traceback (most recent call last):\n  File 'x.py'\n    raise X",
        )
        lines = line.split("\n")
        # First line is the step row.
        self.assertEqual(
            lines[0],
            f"[11] run_swmm_inp  -> [Y/n]: Y  {_FAIL_MARK} swmm exited 1",
        )
        # All subsequent lines are indented at least 4 spaces; the
        # first carries the ``Detail:`` prefix per the PRD example.
        self.assertTrue(lines[1].startswith("    Detail: "))
        for cont in lines[2:]:
            self.assertTrue(cont.startswith("    "), f"continuation line not indented: {cont!r}")


if __name__ == "__main__":
    unittest.main()
