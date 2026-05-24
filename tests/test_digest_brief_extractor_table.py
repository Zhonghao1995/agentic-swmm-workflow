"""Issue #193 item 4: redundant brief extractors are dropped.

``_brief_list_dir`` returns ``f"{len(results)} entries"`` for the
common ``[{...}, {...}]`` shape — which is exactly the string
``tool_registry._list_dir_tool`` already puts into ``summary``. The
generic ``summary.splitlines()[0]`` fallback hits the same value.

``_brief_inspect_plot_options`` is literally a copy of the generic
fallback (``summary.strip().splitlines()[0]``).

These two table entries can be removed without changing observed
brief strings — and removing them shrinks the dispatch table from
six entries to four, making the per-tool override list easier to
audit at a glance.

This test pins:
* Both entries are absent from ``_BRIEF_EXTRACTORS``.
* The user-visible briefs for ``list_dir`` and ``inspect_plot_options``
  are byte-for-byte identical to what the dispatch returned before.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.digest_render import _BRIEF_EXTRACTORS, brief_result


class BriefExtractorTableShrinkTests(unittest.TestCase):
    def test_list_dir_no_longer_in_dispatch_table(self) -> None:
        self.assertNotIn("list_dir", _BRIEF_EXTRACTORS)

    def test_inspect_plot_options_no_longer_in_dispatch_table(self) -> None:
        self.assertNotIn("inspect_plot_options", _BRIEF_EXTRACTORS)

    def test_list_dir_brief_via_fallback_still_says_n_entries(self) -> None:
        # The generic fallback reads the first line of summary, which
        # the live ``_list_dir_tool`` shapes as "<N> entries".
        result = {
            "tool": "list_dir",
            "ok": True,
            "results": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
            "summary": "3 entries",
        }
        self.assertEqual(brief_result("list_dir", result), "3 entries")

    def test_inspect_plot_options_brief_via_fallback_preserved(self) -> None:
        result = {
            "tool": "inspect_plot_options",
            "ok": True,
            "summary": "rain=2 nodes=4 attrs=6",
        }
        self.assertEqual(
            brief_result("inspect_plot_options", result),
            "rain=2 nodes=4 attrs=6",
        )


if __name__ == "__main__":
    unittest.main()
