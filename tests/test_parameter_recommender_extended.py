"""Tests for the extended parameter_recommender (issue #52).

The recommender inspects an INP and returns a structured object:

    {
      "core_required": [...],            # fixed 6-param SWMM-sensitive list
      "recommended":   [...],            # core_required ∪ INP-detected extras
      "rationale":     {param: text},    # one entry per extra (and optional core)
      "infiltration_method": "horton" | "green_ampt" | "curve_number"
    }

The four acceptance bullets from #52 are exercised below:

1. Horton INP → infiltration_method == "horton" and Horton-specific
   parameters (MaxRate / MinRate / Decay) appear in `recommended`.
2. Green-Ampt INP → infiltration_method == "green_ampt" and Green-Ampt
   parameters (Suction / K / IMD) appear in `recommended`.
3. core_required is always a subset of recommended.
4. rationale is non-empty for every parameter in `recommended` that is
   not in `core_required` (the modeller must understand why each extra
   was added).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    REPO_ROOT
    / "skills"
    / "swmm-uncertainty"
    / "scripts"
    / "parameter_recommender.py"
)


HORTON_INP = textwrap.dedent(
    """\
    [TITLE]
    Horton fixture for parameter_recommender tests.

    [OPTIONS]
    FLOW_UNITS           CMS
    INFILTRATION         HORTON
    FLOW_ROUTING         DYNWAVE

    [SUBCATCHMENTS]
    ;;Name  Rain  Outlet  Area  %Imperv  Width  Slope  CurbLen
    S1      RG1   J1      10    50       100    1.0    0

    [SUBAREAS]
    ;;Sub  N-Imperv  N-Perv  S-Imperv  S-Perv  %Zero  Route  PctRouted
    S1     0.013     0.10    0.05      0.05    25     OUTLET 100

    [INFILTRATION]
    ;;Subcatchment  MaxRate  MinRate  Decay  DryTime  MaxInfil
    S1              76       6.0      4.14   3        0
    """
)


GREEN_AMPT_INP = textwrap.dedent(
    """\
    [TITLE]
    Green-Ampt fixture for parameter_recommender tests.

    [OPTIONS]
    FLOW_UNITS           CMS
    INFILTRATION         GREEN_AMPT
    FLOW_ROUTING         DYNWAVE

    [SUBCATCHMENTS]
    ;;Name  Rain  Outlet  Area  %Imperv  Width  Slope  CurbLen
    S1      RG1   J1      10    50       100    1.0    0

    [SUBAREAS]
    ;;Sub  N-Imperv  N-Perv  S-Imperv  S-Perv  %Zero  Route  PctRouted
    S1     0.013     0.10    0.05      0.05    25     OUTLET 100

    [INFILTRATION]
    ;;Subcatchment  Suction  Ksat   IMDmax
    S1              90.82    8.902  0.251
    """
)


CURVE_NUMBER_INP = textwrap.dedent(
    """\
    [TITLE]
    Curve-Number fixture (smoke-only: not asserted in acceptance bullets).

    [OPTIONS]
    FLOW_UNITS           CMS
    INFILTRATION         CURVE_NUMBER
    FLOW_ROUTING         DYNWAVE

    [SUBCATCHMENTS]
    S1      RG1   J1      10    50       100    1.0    0

    [SUBAREAS]
    S1     0.013     0.10    0.05      0.05    25     OUTLET 100

    [INFILTRATION]
    ;;Subcatchment  CurveNum  Ksat  DryTime
    S1              75        0.5   7
    """
)


def _load_module():
    """Import parameter_recommender as a module by file path.

    The script lives under skills/swmm-uncertainty/scripts/ which is not
    on sys.path; importlib.util keeps the test path-agnostic.
    """

    spec = importlib.util.spec_from_file_location(
        "parameter_recommender", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {SCRIPT_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


class ParameterRecommenderExtendedTests(unittest.TestCase):
    """Acceptance-bullet coverage for the extended recommender."""

    def test_script_file_exists(self) -> None:
        self.assertTrue(
            SCRIPT_PATH.exists(),
            msg=(
                f"{SCRIPT_PATH} must exist (issue #52 puts the extended "
                "parameter_recommender under skills/swmm-uncertainty/scripts/)."
            ),
        )

    def test_horton_inp_detects_horton_method_and_horton_params(self) -> None:
        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "horton.inp", HORTON_INP)
            result = mod.recommend(inp)
        self.assertEqual(result["infiltration_method"], "horton")
        for p in ("MaxRate", "MinRate", "Decay"):
            self.assertIn(
                p,
                result["recommended"],
                msg=f"Horton-specific parameter {p!r} missing from recommended set",
            )

    def test_green_ampt_inp_detects_green_ampt_method_and_params(self) -> None:
        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "ga.inp", GREEN_AMPT_INP)
            result = mod.recommend(inp)
        self.assertEqual(result["infiltration_method"], "green_ampt")
        for p in ("Suction", "K", "IMD"):
            self.assertIn(
                p,
                result["recommended"],
                msg=f"Green-Ampt parameter {p!r} missing from recommended set",
            )

    def test_core_required_subset_of_recommended_for_horton(self) -> None:
        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "horton.inp", HORTON_INP)
            result = mod.recommend(inp)
        self.assertTrue(
            set(result["core_required"]).issubset(set(result["recommended"]))
        )

    def test_core_required_subset_of_recommended_for_green_ampt(self) -> None:
        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "ga.inp", GREEN_AMPT_INP)
            result = mod.recommend(inp)
        self.assertTrue(
            set(result["core_required"]).issubset(set(result["recommended"]))
        )

    def test_rationale_nonempty_for_every_recommended_extra(self) -> None:
        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "ga.inp", GREEN_AMPT_INP)
            result = mod.recommend(inp)
        extras = set(result["recommended"]) - set(result["core_required"])
        self.assertGreater(len(extras), 0, msg="expected at least one extra for GA INP")
        for p in extras:
            self.assertIn(
                p,
                result["rationale"],
                msg=f"rationale missing for recommended extra {p!r}",
            )
            text = result["rationale"][p]
            self.assertIsInstance(text, str)
            self.assertTrue(text.strip(), msg=f"rationale for {p!r} is blank")

    def test_core_required_is_the_hardcoded_six(self) -> None:
        """The 6-param SWMM-sensitive list is fixed per #52 spec."""

        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "horton.inp", HORTON_INP)
            result = mod.recommend(inp)
        self.assertEqual(
            sorted(result["core_required"]),
            sorted([
                "N-Imperv",
                "S-Imperv",
                "Pct-Imperv",
                "Width",
                "MaxRate",
                "MinRate",
            ]),
        )

    def test_curve_number_inp_is_detected(self) -> None:
        """Smoke: a CN INP is recognised so downstream consumers can branch."""

        mod = _load_module()
        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "cn.inp", CURVE_NUMBER_INP)
            result = mod.recommend(inp)
        self.assertEqual(result["infiltration_method"], "curve_number")

    def test_cli_outputs_structured_json_for_horton(self) -> None:
        """`python parameter_recommender.py --inp X` emits the structured JSON."""

        with TemporaryDirectory() as tmp:
            inp = _write(Path(tmp) / "horton.inp", HORTON_INP)
            proc = subprocess.run(
                [sys.executable, str(SCRIPT_PATH), "--inp", str(inp)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, 0, msg=f"stderr={proc.stderr}")
        data = json.loads(proc.stdout)
        self.assertIn("core_required", data)
        self.assertIn("recommended", data)
        self.assertIn("rationale", data)
        self.assertEqual(data["infiltration_method"], "horton")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
