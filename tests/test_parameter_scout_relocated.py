"""Locks in the slice-4 (#49) move: parameter_scout left swmm-calibration and
the swmm-uncertainty skill owns sensitivity analysis.

Per the PRD, swmm-calibration answers "which parameter set best matches
observations?" while swmm-uncertainty answers "how much output uncertainty
is induced by parameter uncertainty?" — and sensitivity analysis (OAT,
Morris, Sobol') belongs on the uncertainty side. These assertions are
structural so the move can't silently regress.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

CALIBRATION_SCRIPTS = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
UNCERTAINTY_SCRIPTS = REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts"
CALIBRATION_SERVER = REPO_ROOT / "mcp" / "swmm-calibration" / "server.js"
UNCERTAINTY_SERVER = REPO_ROOT / "mcp" / "swmm-uncertainty" / "server.js"
CALIBRATION_SKILL_MD = REPO_ROOT / "skills" / "swmm-calibration" / "SKILL.md"


class ParameterScoutRelocatedTests(unittest.TestCase):
    """Structural guardrails for the parameter_scout -> sensitivity.py move."""

    def test_parameter_scout_no_longer_in_calibration_scripts(self) -> None:
        scout = CALIBRATION_SCRIPTS / "parameter_scout.py"
        self.assertFalse(
            scout.exists(),
            msg=(
                "parameter_scout.py must be removed from swmm-calibration/scripts "
                "after #49 (it moved to swmm-uncertainty as sensitivity.py)."
            ),
        )

    def test_sensitivity_script_lives_in_uncertainty_scripts(self) -> None:
        sensitivity = UNCERTAINTY_SCRIPTS / "sensitivity.py"
        self.assertTrue(
            sensitivity.exists(),
            msg=(
                "Slice 4 must add skills/swmm-uncertainty/scripts/sensitivity.py "
                "(unified OAT/Morris/Sobol' entry point)."
            ),
        )

    def test_calibration_server_no_longer_registers_parameter_scout(self) -> None:
        src = CALIBRATION_SERVER.read_text(encoding="utf-8")
        # The acceptance criterion: `grep parameter_scout mcp/swmm-calibration/server.js`
        # returns 0 hits.
        self.assertNotIn(
            "parameter_scout",
            src,
            msg="parameter_scout must be cleanly excised from the calibration MCP server.",
        )
        self.assertNotIn(
            "swmm_parameter_scout",
            src,
            msg="The swmm_parameter_scout tool registration must be removed.",
        )

    def test_uncertainty_server_exposes_three_sensitivity_tools(self) -> None:
        self.assertTrue(
            UNCERTAINTY_SERVER.exists(),
            msg=(
                "Slice 4 must add an MCP server at mcp/swmm-uncertainty/server.js "
                "that exposes the three sensitivity tools."
            ),
        )
        src = UNCERTAINTY_SERVER.read_text(encoding="utf-8")
        # Acceptance criterion: >=3 occurrences of swmm_sensitivity_.
        self.assertIn("swmm_sensitivity_oat", src)
        self.assertIn("swmm_sensitivity_morris", src)
        self.assertIn("swmm_sensitivity_sobol", src)
        hits = re.findall(r"swmm_sensitivity_(?:oat|morris|sobol)", src)
        self.assertGreaterEqual(
            len(hits),
            3,
            msg=(
                "Expected at least three swmm_sensitivity_* references in "
                f"mcp/swmm-uncertainty/server.js (got {len(hits)})."
            ),
        )

    def test_calibration_skill_md_no_longer_lists_parameter_scout(self) -> None:
        skill_text = CALIBRATION_SKILL_MD.read_text(encoding="utf-8")
        self.assertNotIn(
            "parameter_scout",
            skill_text,
            msg=(
                "skills/swmm-calibration/SKILL.md still mentions parameter_scout; "
                "after #49 it should point at swmm-uncertainty for SA."
            ),
        )
        self.assertNotIn(
            "swmm_parameter_scout",
            skill_text,
            msg=(
                "skills/swmm-calibration/SKILL.md still lists the "
                "swmm_parameter_scout MCP tool; it was removed in #49."
            ),
        )


if __name__ == "__main__":
    unittest.main()
