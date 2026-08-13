"""The Word deliverable had no hydraulics in it.

A user asked for a report of a successful run and got provenance, QA gates,
and no node flows, then reasonably asked where they were. They were in
model.rpt the whole time: SWMM computed them and nothing downstream looked.

The report script is stdlib + python-docx + PyYAML by rule and cannot parse a
.rpt, so extraction happens on the agentic_swmm side and arrives as JSON. These
tests pin that hand-off.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.reporting.hydraulic_summary import (
    build_hydraulic_summary,
    find_rpt,
    flow_units,
    write_hydraulic_summary,
)

RPT = """
  Flow Units ............... CMS

  ****************************
  Node Inflow Summary
  ****************************

  ---------------------------------------------------------------------
                                  Maximum  Maximum
                                  Lateral    Total
                     Type          Inflow   Inflow
  ---------------------------------------------------------------------
  J11        JUNCTION    0.007    0.061      10  03:15    0.0533   0.489   0.016
  J29        JUNCTION    0.004    0.020      10  03:15    0.034    0.484  -0.010
  OU2        OUTFALL     0.000    0.061      10  03:20    0.000    0.484   0.000


  ****************************
  Outfall Loading Summary
  ****************************

  ---------------------------------------------------------------------
                     Flow       Avg       Max     Total
  ---------------------------------------------------------------------
  OU2        32.71    0.004    0.061    0.484
  OU1        10.00    0.001    0.010    0.100
  System     42.71    0.005    0.071    0.584


  ****************************
  Link Flow Summary
  ****************************

  ---------------------------------------------------------------------
                     Type      Maximum
  ---------------------------------------------------------------------
  C10        CONDUIT    0.061      10  03:15    1.20    0.85    0.60
  C11        CONDUIT    0.020      10  03:10    0.90    0.30    0.25
"""


class ExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name)
        stage = self.run_dir / "06_runner"
        stage.mkdir()
        self.rpt = stage / "model.rpt"
        self.rpt.write_text(RPT, encoding="utf-8")

    def test_finds_the_rpt_in_the_canonical_runner_stage(self) -> None:
        self.assertEqual(find_rpt(self.run_dir), self.rpt)

    def test_flow_units_are_carried_through(self) -> None:
        # A peak inflow of 0.002 with no unit is not a result anyone can use.
        self.assertEqual(flow_units(RPT), "CMS")

    def test_nodes_are_ranked_by_peak_and_carry_the_time_of_peak(self) -> None:
        data = build_hydraulic_summary(self.rpt, top_n=2)
        self.assertEqual([row["node"] for row in data["nodes"]], ["J11", "OU2"])
        self.assertEqual(data["nodes"][0]["time_of_max"], "10 03:15")

    def test_counts_report_the_whole_table_not_just_what_is_shown(self) -> None:
        # "top 2 of 3" is honest; showing 2 and implying that is all is not.
        data = build_hydraulic_summary(self.rpt, top_n=2)
        self.assertEqual(data["counts"]["nodes"], 3)
        self.assertEqual(len(data["nodes"]), 2)

    def test_outfalls_and_links_are_extracted(self) -> None:
        data = build_hydraulic_summary(self.rpt)
        self.assertEqual([row["node"] for row in data["outfalls"]], ["OU2", "OU1"])
        self.assertEqual(data["links"][0]["link"], "C10")

    def test_write_lands_in_the_audit_stage(self) -> None:
        out = write_hydraulic_summary(self.run_dir)
        self.assertEqual(out, self.run_dir / "09_audit" / "hydraulic_summary.json")
        payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["flow_units"], "CMS")

    def test_a_run_with_no_rpt_is_not_an_error(self) -> None:
        # The report generator must not be the thing that fails a run that
        # already succeeded.
        with TemporaryDirectory() as empty:
            self.assertIsNone(write_hydraulic_summary(Path(empty)))

    def test_an_unparseable_rpt_yields_no_artifact_rather_than_a_crash(self) -> None:
        # Same contract as a missing .rpt: no artifact, no exception. The
        # report section then says no summary was extracted instead of
        # printing an empty table that reads like "the model produced
        # nothing".
        self.rpt.write_text("not a SWMM report at all", encoding="utf-8")
        self.assertIsNone(write_hydraulic_summary(self.run_dir))
        self.assertFalse((self.run_dir / "09_audit" / "hydraulic_summary.json").exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
