"""Tests for WQ sections in the audit experiment_note.md.

Covers:
1. WQ-enabled run: ``experiment_note.md`` includes ``## Pollutant Loads``
   section with continuity errors and load tables.
2. Non-WQ run: no ``## Pollutant Loads`` section (zero behavioral change
   for hydrology-only runs).
3. ``render_pollutant_loads_section`` with and without wq_loads data.

Uses the audit pipeline's standalone ``render_note`` and
``render_pollutant_loads_section`` functions directly (no engine required).
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCRIPT = (
    REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"
)
WQ_RPT = REPO_ROOT / "tests" / "fixtures" / "wq" / "wq_smoke.rpt"


def _load_audit():
    spec = importlib.util.spec_from_file_location("_audit_run_wq_test", AUDIT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {AUDIT_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    _prev = sys.modules.get("_audit_run_wq_test")
    sys.modules["_audit_run_wq_test"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if _prev is None:
            sys.modules.pop("_audit_run_wq_test", None)
        else:
            sys.modules["_audit_run_wq_test"] = _prev
    return module


class RenderPollutantLoadsSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = _load_audit()

    def test_returns_empty_string_when_wq_loads_none(self) -> None:
        result = self.audit.render_pollutant_loads_section(None)
        self.assertEqual(result, "")

    def test_returns_empty_string_when_wq_not_present(self) -> None:
        result = self.audit.render_pollutant_loads_section({"ok": True, "wq_present": False})
        self.assertEqual(result, "")

    def test_wq_section_contains_heading(self) -> None:
        wq_loads = {
            "ok": True,
            "wq_present": True,
            "pollutants": ["TSS"],
            "runoff_quality_continuity": [
                {"metric": "Continuity Error (%)", "values": {"TSS": 0.0}},
            ],
            "quality_routing_continuity": [
                {"metric": "Continuity Error (%)", "values": {"TSS": -38.789}},
            ],
            "subcatchment_washoff": [
                {"name": "S1", "loads": {"TSS": 0.109}},
                {"name": "S2", "loads": {"TSS": 0.094}},
            ],
            "link_loads": [],
            "outfall_loads": [
                {"node": "OF1", "flow_freq_pct": 84.43, "avg_flow": 0.026,
                 "max_flow": 0.058, "total_volume_10_6_ltr": 0.081,
                 "pollutant_loads": {"TSS": 0.524}},
            ],
        }
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertIn("## Pollutant Loads", section)

    def test_wq_section_contains_pollutant_name(self) -> None:
        wq_loads = {
            "ok": True,
            "wq_present": True,
            "pollutants": ["TSS"],
            "runoff_quality_continuity": [],
            "quality_routing_continuity": [],
            "subcatchment_washoff": [{"name": "S1", "loads": {"TSS": 0.109}}],
            "link_loads": [],
            "outfall_loads": [],
        }
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertIn("TSS", section)

    def test_wq_section_contains_continuity_error_table(self) -> None:
        wq_loads = {
            "ok": True,
            "wq_present": True,
            "pollutants": ["TSS"],
            "runoff_quality_continuity": [
                {"metric": "Continuity Error (%)", "values": {"TSS": 0.0}},
            ],
            "quality_routing_continuity": [
                {"metric": "Continuity Error (%)", "values": {"TSS": -38.789}},
            ],
            "subcatchment_washoff": [],
            "link_loads": [],
            "outfall_loads": [],
        }
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertIn("WQ Continuity Errors", section)
        self.assertIn("-38.789", section)

    def test_wq_section_contains_washoff_table(self) -> None:
        wq_loads = {
            "ok": True,
            "wq_present": True,
            "pollutants": ["TSS"],
            "runoff_quality_continuity": [],
            "quality_routing_continuity": [],
            "subcatchment_washoff": [
                {"name": "S1", "loads": {"TSS": 0.109}},
                {"name": "S3", "loads": {"TSS": 0.112}},
            ],
            "link_loads": [],
            "outfall_loads": [],
        }
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertIn("Subcatchment Washoff", section)
        self.assertIn("S1", section)
        self.assertIn("0.109", section)

    def test_wq_section_contains_outfall_load_table(self) -> None:
        wq_loads = {
            "ok": True,
            "wq_present": True,
            "pollutants": ["TSS"],
            "runoff_quality_continuity": [],
            "quality_routing_continuity": [],
            "subcatchment_washoff": [],
            "link_loads": [],
            "outfall_loads": [
                {"node": "OF1", "flow_freq_pct": 84.43, "avg_flow": 0.026,
                 "max_flow": 0.058, "total_volume_10_6_ltr": 0.081,
                 "pollutant_loads": {"TSS": 0.524}},
            ],
        }
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertIn("Outfall Pollutant Loads", section)
        self.assertIn("OF1", section)
        self.assertIn("0.524", section)


@unittest.skipUnless(WQ_RPT.exists(), f"WQ fixture not found at {WQ_RPT}")
class AuditNoteWQIntegrationTests(unittest.TestCase):
    """End-to-end: parse WQ rpt with extract_wq_loads, render audit note."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = _load_audit()

        # Load extract_wq_loads.py directly (same way audit_run does it).
        extract_script = (
            REPO_ROOT / "skills" / "swmm-water-quality" / "scripts" / "extract_wq_loads.py"
        )
        spec = importlib.util.spec_from_file_location("_extract_wq_loads_test", extract_script)
        module = importlib.util.module_from_spec(spec)
        sys.modules["_extract_wq_loads_test"] = module
        spec.loader.exec_module(module)
        sys.modules.pop("_extract_wq_loads_test", None)

        rpt_text = WQ_RPT.read_text(encoding="utf-8")
        cls.wq_loads = module.extract_wq_loads(rpt_text)

    def test_extract_reports_wq_present(self) -> None:
        self.assertTrue(self.wq_loads.get("wq_present"))

    def test_extract_reports_tss_pollutant(self) -> None:
        self.assertIn("TSS", self.wq_loads.get("pollutants", []))

    def test_render_section_includes_pollutant_loads_heading(self) -> None:
        section = self.audit.render_pollutant_loads_section(self.wq_loads)
        self.assertIn("## Pollutant Loads", section)

    def test_render_section_includes_routing_continuity_error(self) -> None:
        section = self.audit.render_pollutant_loads_section(self.wq_loads)
        # Quality routing continuity error is -38.789 in the fixture.
        self.assertIn("-38.789", section)

    def test_render_section_includes_subcatchment_s1(self) -> None:
        section = self.audit.render_pollutant_loads_section(self.wq_loads)
        self.assertIn("S1", section)

    def test_render_section_includes_outfall_of1_load(self) -> None:
        section = self.audit.render_pollutant_loads_section(self.wq_loads)
        self.assertIn("OF1", section)
        self.assertIn("0.524", section)


class NonWQAuditNoteTests(unittest.TestCase):
    """Non-WQ runs must not get a Pollutant Loads section."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = _load_audit()

    def test_no_wq_section_when_wq_loads_none(self) -> None:
        # Simulate render_note with wq_loads=None in provenance.
        provenance_fragment = {"metrics": {"wq_loads": None}}
        wq_loads = (provenance_fragment.get("metrics") or {}).get("wq_loads")
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertEqual(section, "")

    def test_no_wq_section_when_wq_not_present(self) -> None:
        provenance_fragment = {"metrics": {"wq_loads": {"ok": True, "wq_present": False}}}
        wq_loads = (provenance_fragment.get("metrics") or {}).get("wq_loads")
        section = self.audit.render_pollutant_loads_section(wq_loads)
        self.assertEqual(section, "")


if __name__ == "__main__":
    unittest.main()
