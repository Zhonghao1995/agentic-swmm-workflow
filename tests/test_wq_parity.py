"""Parity test: extract_wq_loads.py vs rpt_summary parse_variable_section.

Asserts that the stdlib-only skill script and the rpt_summary module
return numerically identical results for the same WQ rpt fixture.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from agentic_swmm.agent.swmm_runtime.rpt_summary import SECTIONS, parse_section, parse_variable_section

REPO_ROOT = Path(__file__).resolve().parents[1]
WQ_RPT = REPO_ROOT / "tests" / "fixtures" / "wq" / "wq_smoke.rpt"
EXTRACT_SCRIPT = REPO_ROOT / "skills" / "swmm-water-quality" / "scripts" / "extract_wq_loads.py"


def _load_extract():
    spec = importlib.util.spec_from_file_location("_extract_wq_parity", EXTRACT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_extract_wq_parity"] = module
    spec.loader.exec_module(module)
    sys.modules.pop("_extract_wq_parity", None)
    return module


@unittest.skipUnless(WQ_RPT.exists(), f"WQ fixture not found at {WQ_RPT}")
class WQParityTests(unittest.TestCase):
    """Numbers from extract_wq_loads == numbers from parse_variable_section."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rpt_text = WQ_RPT.read_text(encoding="utf-8")
        mod = _load_extract()
        cls.wq = mod.extract_wq_loads(cls.rpt_text)

    def test_runoff_quality_continuity_parity(self) -> None:
        rpt_rows = parse_variable_section(self.rpt_text, SECTIONS["Runoff Quality Continuity"])
        script_rows = self.wq["runoff_quality_continuity"]
        self.assertEqual(len(rpt_rows), len(script_rows))
        for rpt_row, script_row in zip(rpt_rows, script_rows):
            self.assertEqual(rpt_row["metric"], script_row["metric"])
            for pol, val in rpt_row["values"].items():
                self.assertAlmostEqual(val, script_row["values"][pol], places=5,
                                       msg=f"Runoff QC metric={rpt_row['metric']} pol={pol}")

    def test_quality_routing_continuity_parity(self) -> None:
        rpt_rows = parse_variable_section(self.rpt_text, SECTIONS["Quality Routing Continuity"])
        script_rows = self.wq["quality_routing_continuity"]
        self.assertEqual(len(rpt_rows), len(script_rows))
        for rpt_row, script_row in zip(rpt_rows, script_rows):
            self.assertEqual(rpt_row["metric"], script_row["metric"])
            for pol, val in rpt_row["values"].items():
                self.assertAlmostEqual(val, script_row["values"][pol], places=5,
                                       msg=f"Routing QC metric={rpt_row['metric']} pol={pol}")

    def test_subcatchment_washoff_parity(self) -> None:
        rpt_rows = parse_variable_section(self.rpt_text, SECTIONS["Subcatchment Washoff Summary"])
        script_rows = {r["name"]: r for r in self.wq["subcatchment_washoff"]}
        self.assertEqual(len(rpt_rows), len(script_rows))
        for rpt_row in rpt_rows:
            name = rpt_row["name"]
            self.assertIn(name, script_rows)
            for pol, val in rpt_row["loads"].items():
                self.assertAlmostEqual(val, script_rows[name]["loads"][pol], places=5,
                                       msg=f"Washoff name={name} pol={pol}")

    def test_link_pollutant_load_parity(self) -> None:
        rpt_rows = parse_variable_section(self.rpt_text, SECTIONS["Link Pollutant Load Summary"])
        script_rows = {r["name"]: r for r in self.wq["link_loads"]}
        self.assertEqual(len(rpt_rows), len(script_rows))
        for rpt_row in rpt_rows:
            name = rpt_row["name"]
            self.assertIn(name, script_rows)
            for pol, val in rpt_row["loads"].items():
                self.assertAlmostEqual(val, script_rows[name]["loads"][pol], places=5,
                                       msg=f"Link loads name={name} pol={pol}")

    def test_outfall_pollutant_load_parity(self) -> None:
        rpt_rows = parse_section(self.rpt_text, SECTIONS["Outfall Loading Summary"])
        script_rows = {r["node"]: r for r in self.wq["outfall_loads"]}
        self.assertEqual(len(rpt_rows), len(script_rows))
        for rpt_row in rpt_rows:
            node = rpt_row["node"]
            self.assertIn(node, script_rows)
            for pol, val in rpt_row["pollutant_loads"].items():
                self.assertAlmostEqual(val, script_rows[node]["pollutant_loads"][pol], places=5,
                                       msg=f"Outfall loads node={node} pol={pol}")


if __name__ == "__main__":
    unittest.main()
