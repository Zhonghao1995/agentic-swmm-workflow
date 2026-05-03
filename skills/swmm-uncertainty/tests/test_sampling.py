#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from sampling import generate_parameter_sets  # noqa: E402


class SamplingTests(unittest.TestCase):
    def test_lhs_generates_one_baseline_trial_for_degenerate_alpha(self) -> None:
        alpha_intervals = {
            "alpha_levels": [1.0],
            "parameters": {
                "p": {
                    "alpha_cuts": [
                        {
                            "alpha": 1.0,
                            "lower": 5.0,
                            "upper": 5.0,
                        }
                    ]
                }
            },
        }
        trials = generate_parameter_sets(alpha_intervals, method="lhs", samples_per_alpha=10, seed=42)
        self.assertEqual(len(trials), 1)
        self.assertEqual(trials[0]["params"], {"p": 5.0})

    def test_boundary_sampling_removes_duplicate_corners(self) -> None:
        alpha_intervals = {
            "alpha_levels": [0.0],
            "parameters": {
                "p": {"alpha_cuts": [{"alpha": 0.0, "lower": 1.0, "upper": 1.0}]},
                "q": {"alpha_cuts": [{"alpha": 0.0, "lower": 2.0, "upper": 4.0}]},
            },
        }
        trials = generate_parameter_sets(alpha_intervals, method="boundary", samples_per_alpha=10, seed=42)
        self.assertEqual(len(trials), 2)
        self.assertEqual({trial["params"]["q"] for trial in trials}, {2.0, 4.0})


if __name__ == "__main__":
    unittest.main()
