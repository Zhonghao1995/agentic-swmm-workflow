"""Projection from real QA artifacts into the thresholds namespace.

The phantom-schema hole (spec: fuzzy-hitl-gates): the evaluator reads
dotted keys like ``continuity.flow_routing`` while the QA writer emits
a ``checks`` list carrying the numbers under ``detail``. The fixture
here is a real run's ``qa_summary.json`` shape (paths anonymized), so
this suite fails loudly if either side drifts again.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from agentic_swmm.hitl.qa_projection import project_qa

FIXTURE = Path(__file__).parent / "fixtures" / "hitl" / "qa_summary_real_shape.json"


class ProjectionTests(unittest.TestCase):
    def test_real_shape_yields_continuity_namespace(self) -> None:
        qa = json.loads(FIXTURE.read_text(encoding="utf-8"))
        derived = project_qa(qa)
        self.assertIn("continuity", derived)
        # Real value in the fixture is -0.004; compared on the absolute
        # value, signed raw kept alongside.
        self.assertAlmostEqual(derived["continuity"]["flow_routing"], 0.004)
        self.assertAlmostEqual(derived["continuity"]["flow_routing_signed"], -0.004)
        self.assertAlmostEqual(derived["continuity"]["runoff_quantity"], 0.13)

    def test_dotted_input_yields_nothing(self) -> None:
        # Synthetic evaluator-test shape: already dotted, nothing to derive.
        self.assertEqual(project_qa({"continuity": {"flow_routing": 6.5}}), {})

    def test_malformed_checks_yield_nothing(self) -> None:
        self.assertEqual(project_qa({"checks": "oops"}), {})
        self.assertEqual(project_qa({"checks": [{"id": "continuity_parsed"}]}), {})

    def test_sensitivity_indices_project_max_first_order(self) -> None:
        sens = {
            "method": "sobol",
            "indices": {
                "imperv": {"S_i": 0.82, "S_T_i": 0.9},
                "width": {"S_i": 0.11, "S_T_i": 0.2},
            },
        }
        derived = project_qa({}, sensitivity_indices=sens)
        self.assertAlmostEqual(
            derived["sensitivity"]["sobol"]["S_i_max"], 0.82
        )

    def test_calibration_summary_projects_metrics(self) -> None:
        # Shape locked by tests/test_calibration_summary_schema.py:
        # KGE is the primary objective, NSE/PBIAS are secondary metrics.
        summary = {
            "primary_objective": "kge",
            "primary_value": 0.41,
            "secondary_metrics": {"nse": 0.38, "pbias_pct": -31.5},
        }
        derived = project_qa({}, calibration_summary=summary)
        self.assertAlmostEqual(derived["calibration"]["kge"], 0.41)
        self.assertAlmostEqual(derived["calibration"]["nse"], 0.38)
        self.assertAlmostEqual(derived["calibration"]["pbias_pct_abs"], 31.5)

    def test_calibration_partial_summary_projects_what_exists(self) -> None:
        summary = {"primary_objective": "rmse", "primary_value": 1.2}
        self.assertEqual(project_qa({}, calibration_summary=summary), {})
        summary = {
            "primary_objective": "rmse",
            "primary_value": 1.2,
            "secondary_metrics": {"nse": 0.7},
        }
        derived = project_qa({}, calibration_summary=summary)
        self.assertEqual(derived["calibration"], {"nse": 0.7})

    def test_malformed_calibration_summary_yields_nothing(self) -> None:
        self.assertEqual(project_qa({}, calibration_summary={"secondary_metrics": "x"}), {})

    def test_non_sobol_sensitivity_ignored(self) -> None:
        sens = {"method": "morris", "indices": {"imperv": {"mu_star": 1.0}}}
        self.assertEqual(project_qa({}, sensitivity_indices=sens), {})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
