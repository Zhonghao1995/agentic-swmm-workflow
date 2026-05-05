from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"


def load_runner_module():
    spec = importlib.util.spec_from_file_location("swmm_runner", RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SwmmRunnerPeakParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = load_runner_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_rpt(self, text: str) -> Path:
        rpt = self.tmp_path / "model.rpt"
        rpt.write_text(text, encoding="utf-8")
        return rpt

    def test_prefers_node_inflow_summary_over_node_depth_summary(self) -> None:
        rpt = self.write_rpt(
            """
            Node Depth Summary
            ------------------------------------------------
              O1              98.54       99.00

            ***** Node Inflow Summary *****
            ------------------------------------------------
              O1              OUTFALL       0.001       0.007      2    12:47

            ***** Outfall Loading Summary *****
            ------------------------------------------------
              O1                3.0         4.0         5.0         6.0
            """
        )

        peak = self.runner.parse_peak_from_rpt(rpt, "O1")

        self.assertEqual(peak["source"], "Node Inflow Summary")
        self.assertEqual(peak["peak"], 0.007)
        self.assertEqual(peak["time_hhmm"], "12:47")

    def test_uses_outfall_loading_summary_as_fallback(self) -> None:
        rpt = self.write_rpt(
            """
            ***** Outfall Loading Summary *****
            ------------------------------------------------
              OF1               10.0        20.0        30.5        40.0
            """
        )

        peak = self.runner.parse_peak_from_rpt(rpt, "OF1")

        self.assertEqual(peak["source"], "Outfall Loading Summary")
        self.assertEqual(peak["peak"], 30.5)
        self.assertIsNone(peak["time_hhmm"])

    def test_does_not_use_node_depth_summary_as_peak_flow(self) -> None:
        rpt = self.write_rpt(
            """
            ***** Node Depth Summary *****
            ------------------------------------------------
              O1              98.54       99.00
            """
        )

        peak = self.runner.parse_peak_from_rpt(rpt, "O1")

        self.assertIsNone(peak["peak"])
        self.assertIsNone(peak["source"])


if __name__ == "__main__":
    unittest.main()
