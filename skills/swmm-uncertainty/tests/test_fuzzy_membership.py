#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from fuzzy_membership import build_alpha_intervals, resolve_fuzzy_space  # noqa: E402


class FuzzyMembershipTests(unittest.TestCase):
    def test_triangular_defaults_to_model_baseline(self) -> None:
        params = resolve_fuzzy_space(
            {
                "parameters": {
                    "pct_imperv_s1": {
                        "type": "triangular",
                        "lower": 15.0,
                        "upper": 40.0,
                        "baseline": "from_model",
                    }
                }
            },
            {"pct_imperv_s1": 25.0},
        )
        interval = params["pct_imperv_s1"].alpha_interval(0.5)
        self.assertEqual(interval, (20.0, 32.5))

    def test_triangular_rejects_baseline_outside_bounds(self) -> None:
        with self.assertRaises(ValueError):
            resolve_fuzzy_space(
                {"parameters": {"p": {"type": "triangular", "lower": 1.0, "upper": 2.0}}},
                {"p": 3.0},
            )

    def test_trapezoidal_core_width_around_baseline(self) -> None:
        params = resolve_fuzzy_space(
            {
                "parameters": {
                    "n": {
                        "type": "trapezoidal",
                        "lower": 0.01,
                        "upper": 0.03,
                        "core_width": 0.004,
                    }
                }
            },
            {"n": 0.02},
        )
        interval = params["n"].alpha_interval(0.5)
        self.assertAlmostEqual(interval[0], 0.014)
        self.assertAlmostEqual(interval[1], 0.026)

    def test_build_alpha_intervals(self) -> None:
        params = resolve_fuzzy_space(
            {"parameters": {"p": {"type": "triangular", "lower": 0, "upper": 10}}},
            {"p": 5},
        )
        intervals = build_alpha_intervals(params, [0.0, 1.0])
        cuts = intervals["parameters"]["p"]["alpha_cuts"]
        self.assertEqual(cuts[0]["lower"], 0.0)
        self.assertEqual(cuts[1]["lower"], 5.0)
        self.assertEqual(cuts[1]["upper"], 5.0)


if __name__ == "__main__":
    unittest.main()
