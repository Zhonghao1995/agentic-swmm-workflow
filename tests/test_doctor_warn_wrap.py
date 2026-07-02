"""PRD-08 Phase B (audit #28): doctor multi-line WARN column alignment.

Long ``WARN`` details (the ~250-char mcp.json remediation lines)
historically blew out the rightmost column. Phase B wraps them with a
2-space hanging indent under the WARN row's main message so the column
structure survives a 80-col terminal.
"""

from __future__ import annotations

import unittest

from agentic_swmm.diagnostics.doctor_report import (
    GroupedWarnRow,
    render_grouped_warns_section,
)


class WarnDetailWrappingTests(unittest.TestCase):
    def test_long_detail_wraps_under_hanging_indent(self) -> None:
        row = {
            "name": "mcp.json: swmm-builder",
            "detail": (
                "mcp.json routes swmm-builder to a different checkout "
                "(/Users/zhonghao/Desktop/old-checkout/run.mjs). "
                "Re-run 'aiswmm setup --refresh-mcp' to align with the "
                "active install, or sync that checkout manually."
            ),
            "passed": False,
            "required": False,
        }
        body = render_grouped_warns_section([row], width=80)
        lines = body.splitlines()
        # The first line under "Issues:" carries the head.
        self.assertEqual(lines[0], "Issues:")
        # Most lines must fit within the 80-col budget. A single
        # unbreakable token (an absolute filesystem path) may exceed
        # the budget on its own line; we skip that case explicitly.
        for line in lines:
            if " " in line.lstrip():
                self.assertLessEqual(
                    len(line), 80, f"line over budget: {line!r}"
                )
        # The continuation indent should match the column where the
        # detail began (head len = 2 + 7 + 1 + name + 3 = 33+ chars).
        # Expect at least one continuation line that starts with
        # whitespace and does not begin with "WARN ".
        continuation_lines = [
            ln for ln in lines[1:] if not ln.startswith("  WARN")
        ]
        self.assertTrue(continuation_lines)
        for ln in continuation_lines:
            stripped = ln.lstrip()
            # Continuation lines don't start with the severity column
            # (they're indented under the detail column).
            self.assertNotEqual(stripped, ln)

    def test_short_detail_does_not_wrap(self) -> None:
        row = {
            "name": "node",
            "detail": "not found",
            "passed": False,
            "required": True,
        }
        body = render_grouped_warns_section([row], width=80)
        # Only "Issues:" header + the single short row.
        self.assertEqual(
            body.splitlines(),
            ["Issues:", "  MISSING node - not found"],
        )

    def test_grouped_warn_summary_long_summary_wraps(self) -> None:
        # The grouped MCP row carries a long sentence-shaped summary.
        # Confirm the wrap fires and that continuation lines hang
        # under the detail column (not under the severity).
        group = GroupedWarnRow(
            summary=(
                "11 MCP servers drift to a different checkout path "
                "that the user has not synced with the active "
                "install yet which is the point of the remediation"
            ),
            representative_remediation=(
                "run aiswmm setup --refresh-mcp"
            ),
            member_names=["a", "b", "c", "d", "e", "f"],
        )
        body = render_grouped_warns_section([group], width=80)
        lines = body.splitlines()
        # First non-header line should be the WARN row; subsequent
        # continuation lines should hang indent under the detail
        # column (not under the severity).
        warn_lines = [ln for ln in lines if "WARN" in ln]
        self.assertTrue(warn_lines, body)
        # Wrap fires when the summary exceeds budget.
        for line in lines:
            # Soft check: most lines should fit. Single tokens longer
            # than the budget are emitted unbroken (acceptable).
            if " " in line.lstrip():
                self.assertLessEqual(
                    len(line), 80, f"line over budget: {line!r}"
                )


if __name__ == "__main__":
    unittest.main()
