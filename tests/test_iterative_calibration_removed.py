"""Guard test for bug #237: iterative_calibration.py was a dead script that
subprocess-called parameter_scout.py (deleted in #65).

Both the dead script and the stale README example must be gone. These
assertions match the style of tests/test_parameter_scout_relocated.py.
"""

from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

ITERATIVE_CAL = (
    REPO_ROOT / "skills" / "swmm-calibration" / "scripts" / "iterative_calibration.py"
)
CALIBRATION_README = REPO_ROOT / "examples" / "calibration" / "README.md"


class IterativeCalibrationRemovedTests(unittest.TestCase):
    """Structural guardrails ensuring the dead iterative_calibration script is gone."""

    def test_iterative_calibration_script_does_not_exist(self) -> None:
        self.assertFalse(
            ITERATIVE_CAL.exists(),
            msg=(
                "skills/swmm-calibration/scripts/iterative_calibration.py must be "
                "deleted (bug #237): it subprocess-calls the already-deleted "
                "parameter_scout.py and is fully orphaned."
            ),
        )

    def test_calibration_readme_has_no_parameter_scout_reference(self) -> None:
        readme_text = CALIBRATION_README.read_text(encoding="utf-8")
        self.assertNotIn(
            "parameter_scout",
            readme_text,
            msg=(
                "examples/calibration/README.md still references parameter_scout.py "
                "(bug #237). The example should be updated to point at the relocated "
                "OAT tool: skills/swmm-uncertainty/scripts/sensitivity.py --method oat"
            ),
        )


if __name__ == "__main__":
    unittest.main()
