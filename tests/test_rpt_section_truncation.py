"""Regression tests: a malformed rpt row must not truncate the section.

Bug (found 2026-08-08 static sweep, HIGH): the section walker in
``rpt_summary.py`` treated ANY token-count mismatch as an end-of-section
marker and ``break``-ed the whole row loop. But SWMM writes real data
rows with odd token counts — the canonical example is a zero-volume
Node Inflow row where the volume column is printed with a unit suffix
(``0.000 ltr``), giving 11 tokens instead of the schema's exact 9. One
such row mid-section silently dropped EVERY node after it, so
``read_rpt_summary`` / compare / design-review could report a wrong
"highest max_total_inflow" with ``ok: true`` and no signal at all.

Fix under test:

* ``parse_section_with_stats`` skips the mismatched row, keeps walking,
  and returns ``(rows, skipped)``.
* ``parse_section`` keeps its historic ``list`` signature (thin wrapper).
* The ``read_rpt_summary`` handler surfaces ``skipped_malformed_rows``
  and appends a completeness note to ``summary`` when rows were skipped.
* Genuine terminators (blank line, ``---``, ``***`` banner) still end
  the section — the fix must NOT make the walker run past the section.
* The design-review twin parser (``_parse_rpt_section`` in
  ``skills/swmm-design-review/scripts/design_review.py``) gets the same
  skip-not-break behavior.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import unittest
from pathlib import Path

from agentic_swmm.agent.swmm_runtime.rpt_summary import (
    SECTIONS,
    parse_section,
    parse_section_with_stats,
)
from agentic_swmm.agent.tool_handlers.swmm_rpt import _read_rpt_summary_tool
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


# ---------------------------------------------------------------------------
# Fixture: Node Inflow Summary with a unit-suffix row in the MIDDLE.
#
# Row 2 (``N_ZERO``) mimics the SWMM zero-volume formatting quirk: the
# two volume columns carry a ``ltr`` unit token, so the row splits into
# 11 tokens instead of the exact 9 the schema expects. Before the fix,
# the walker break-ed here and ``N_AFTER`` — which holds the LARGEST
# max_total_inflow in the section — was silently dropped.
# ---------------------------------------------------------------------------

_TRUNCATION_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  *******************
  Node Inflow Summary
  *******************

  -------------------------------------------------------------------------------------------------
                                  Maximum  Maximum                  Lateral       Total        Flow
                                  Lateral    Total  Time of Max      Inflow      Inflow     Balance
                                   Inflow   Inflow   Occurrence      Volume      Volume       Error
  Node                 Type           LPS      LPS  days hr:min    10^6 ltr    10^6 ltr     Percent
  -------------------------------------------------------------------------------------------------
  N_BEFORE             JUNCTION      9.71   120.00     0  00:11      0.0058       0.500       0.000
  N_ZERO               JUNCTION      0.00     0.00     0  00:00       0.000 ltr    0.000 ltr  0.000
  N_AFTER              JUNCTION     12.50   500.00     0  00:15      0.0100       0.900       0.000


  ********************
  Link Flow Summary
  ********************

  -----------------------------------------------------------------------------
                                 Maximum  Time of Max   Maximum    Max/    Max/
                                  |Flow|   Occurrence   |Veloc|    Full    Full
  Link                 Type          LPS  days hr:min     m/sec    Flow   Depth
  -----------------------------------------------------------------------------
  L1                   CONDUIT    103.46     0  00:21      5.85    2.21    1.00
"""


_SCRATCH_ROOT = repo_root() / "runs" / "_test_rpt_truncation"


def setUpModule() -> None:  # pragma: no cover — pytest module fixture
    if _SCRATCH_ROOT.exists():
        shutil.rmtree(_SCRATCH_ROOT, ignore_errors=True)


def tearDownModule() -> None:  # pragma: no cover — pytest module fixture
    if _SCRATCH_ROOT.exists():
        shutil.rmtree(_SCRATCH_ROOT, ignore_errors=True)


class ParseSectionSkipTests(unittest.TestCase):
    """Canonical parser: mismatched row skips, later rows survive."""

    def test_rows_after_malformed_row_are_kept(self) -> None:
        schema = SECTIONS["Node Inflow Summary"]
        rows = parse_section(_TRUNCATION_RPT, schema)
        names = [r["node"] for r in rows]
        # Pre-fix behavior: names == ["N_BEFORE"] — N_AFTER vanished.
        self.assertEqual(names, ["N_BEFORE", "N_AFTER"])

    def test_skip_count_is_reported(self) -> None:
        schema = SECTIONS["Node Inflow Summary"]
        rows, skipped = parse_section_with_stats(_TRUNCATION_RPT, schema)
        self.assertEqual(len(rows), 2)
        self.assertEqual(skipped, 1)

    def test_true_ranking_includes_post_skip_row(self) -> None:
        """The bug's real-world symptom: wrong 'highest inflow' answer."""
        schema = SECTIONS["Node Inflow Summary"]
        rows = parse_section(_TRUNCATION_RPT, schema)
        top = max(rows, key=lambda r: r["max_total_inflow"])
        self.assertEqual(top["node"], "N_AFTER")
        self.assertAlmostEqual(top["max_total_inflow"], 500.0)

    def test_clean_section_reports_zero_skipped(self) -> None:
        clean = _TRUNCATION_RPT.replace(
            "  N_ZERO               JUNCTION      0.00     0.00     0  00:00"
            "       0.000 ltr    0.000 ltr  0.000\n",
            "",
        )
        schema = SECTIONS["Node Inflow Summary"]
        rows, skipped = parse_section_with_stats(clean, schema)
        self.assertEqual([r["node"] for r in rows], ["N_BEFORE", "N_AFTER"])
        self.assertEqual(skipped, 0)

    def test_blank_line_still_terminates_section(self) -> None:
        """The fix must not run past the section: the Link Flow banner
        after the blank line must never leak into Node Inflow rows."""
        schema = SECTIONS["Node Inflow Summary"]
        rows = parse_section(_TRUNCATION_RPT, schema)
        self.assertNotIn("L1", [r["node"] for r in rows])

    def test_next_section_still_parses_independently(self) -> None:
        schema = SECTIONS["Link Flow Summary"]
        rows, skipped = parse_section_with_stats(_TRUNCATION_RPT, schema)
        self.assertEqual([r["link"] for r in rows], ["L1"])
        self.assertEqual(skipped, 0)


class HandlerCompletenessSignalTests(unittest.TestCase):
    """``read_rpt_summary`` surfaces the skip count to the planner."""

    def _run_handler(self, content: str) -> dict:
        scratch = _SCRATCH_ROOT / "handler"
        scratch.mkdir(parents=True, exist_ok=True)
        target = scratch / "model.rpt"
        target.write_text(content, encoding="utf-8")
        call = ToolCall(
            name="read_rpt_summary",
            args={
                "rpt_path": str(target.relative_to(repo_root())),
                "section": "Node Inflow Summary",
                "top_n": 10,
            },
        )
        return _read_rpt_summary_tool(call, scratch)

    def test_payload_exposes_skipped_malformed_rows(self) -> None:
        result = self._run_handler(_TRUNCATION_RPT)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["skipped_malformed_rows"], 1)
        self.assertIn("skipped 1 unparseable row", result["summary"])
        # Default sort is max_total_inflow desc — N_AFTER must lead.
        self.assertEqual(result["rows"][0]["node"], "N_AFTER")

    def test_clean_rpt_omits_skip_key(self) -> None:
        clean = _TRUNCATION_RPT.replace(
            "  N_ZERO               JUNCTION      0.00     0.00     0  00:00"
            "       0.000 ltr    0.000 ltr  0.000\n",
            "",
        )
        result = self._run_handler(clean)
        self.assertTrue(result["ok"])
        self.assertNotIn("skipped_malformed_rows", result)
        self.assertNotIn("skipped", result["summary"])


class DesignReviewTwinTests(unittest.TestCase):
    """The design-review copy of the parser gets the same treatment."""

    @classmethod
    def setUpClass(cls) -> None:
        script = (
            repo_root()
            / "skills/swmm-design-review/scripts/design_review.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_design_review_truncation_test", script
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        cls.mod = module

    @classmethod
    def tearDownClass(cls) -> None:
        sys.modules.pop("_design_review_truncation_test", None)

    def test_node_inflow_rows_after_malformed_row_are_kept(self) -> None:
        rows = self.mod._parse_node_inflow_summary(_TRUNCATION_RPT)
        self.assertIsNotNone(rows)
        names = [r["node"] for r in rows]
        self.assertIn("N_AFTER", names)
        self.assertIn("N_BEFORE", names)
        self.assertNotIn("N_ZERO", names)

    def test_raw_section_walker_skips_not_breaks(self) -> None:
        raw = self.mod._parse_rpt_section(
            _TRUNCATION_RPT, "Node Inflow Summary", 9
        )
        first_tokens = [r[0] for r in raw]
        self.assertEqual(first_tokens, ["N_BEFORE", "N_AFTER"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
