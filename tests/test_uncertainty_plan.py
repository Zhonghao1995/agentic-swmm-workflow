"""Tests for ``agentic_swmm.agent.swmm_runtime.uncertainty_plan`` (PRD-06 B.4).

Contract:
- ``plan_uncertainty_run`` with Morris + 3 params + n=20 returns a
  positive sample count.
- Same for Sobol with 2 params + n=8.
- Missing SALib falls back to ``samples=[]`` with
  ``provenance["error"]`` rather than crashing.
- ``base_inp_hash`` is deterministic for the same file.
- Seed is honoured — same seed -> same samples.
- CLI smoke: ``aiswmm uncertainty plan`` writes a JSON file.
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from agentic_swmm.agent.swmm_runtime.uncertainty_plan import (
    plan_uncertainty_run,
)


def _has_salib() -> bool:
    return importlib.util.find_spec("SALib") is not None


@pytest.mark.skipif(not _has_salib(), reason="SALib not installed")
class MorrisTests(unittest.TestCase):
    def test_morris_with_three_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\ntest\n", encoding="utf-8")
            plan = plan_uncertainty_run(
                inp,
                {
                    "manning_n": (0.01, 0.03),
                    "imdmax": (0.1, 0.4),
                    "ksat": (1.0, 10.0),
                },
                method="morris",
                n_samples=20,
                seed=42,
            )
        self.assertGreater(plan.n_samples_actual, 0)
        self.assertEqual(plan.method, "morris")
        self.assertEqual(plan.parameter_names, ["manning_n", "imdmax", "ksat"])
        # Each sample must include every parameter name as a key.
        first = plan.samples[0]
        self.assertEqual(set(first.keys()), {"manning_n", "imdmax", "ksat"})
        # Provenance: deterministic input hash, salib version recorded.
        self.assertTrue(plan.provenance["base_inp_hash"].startswith("sha256:"))
        self.assertIsNotNone(plan.provenance["salib_version"])
        self.assertNotIn("error", plan.provenance)


@pytest.mark.skipif(not _has_salib(), reason="SALib not installed")
class SobolTests(unittest.TestCase):
    def test_sobol_with_two_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\ntest\n", encoding="utf-8")
            plan = plan_uncertainty_run(
                inp,
                {
                    "manning_n": (0.01, 0.03),
                    "imdmax": (0.1, 0.4),
                },
                method="sobol",
                n_samples=8,
                seed=7,
            )
        self.assertGreater(plan.n_samples_actual, 0)
        self.assertEqual(plan.method, "sobol")


class MissingSalibFallbackTests(unittest.TestCase):
    """Even without SALib the verb must return a valid (empty) plan."""

    def test_missing_salib_returns_empty_plan_with_error_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\ntest\n", encoding="utf-8")

            # Force the import to fail by removing SALib from sys.modules
            # and pointing the meta_path at a reject hook. Cleaner than
            # patching at the call site because the module imports SALib
            # lazily inside ``_draw_samples``.
            from agentic_swmm.agent.swmm_runtime import uncertainty_plan

            with mock.patch.object(
                uncertainty_plan,
                "_draw_samples",
                side_effect=uncertainty_plan._SalibMissingError("forced"),
            ):
                plan = plan_uncertainty_run(
                    inp,
                    {"manning_n": (0.01, 0.03)},
                    method="morris",
                    n_samples=10,
                    seed=0,
                )
        self.assertEqual(plan.samples, [])
        self.assertEqual(plan.n_samples_actual, 0)
        self.assertIn("error", plan.provenance)
        self.assertIn("forced", plan.provenance["error"])
        self.assertIsNone(plan.provenance["salib_version"])


class InpHashTests(unittest.TestCase):
    def test_same_inp_yields_same_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            content = "[TITLE]\nsanity\n"
            inp.write_text(content, encoding="utf-8")
            inp2 = Path(tmp) / "model_dup.inp"
            inp2.write_text(content, encoding="utf-8")

            from agentic_swmm.agent.swmm_runtime.uncertainty_plan import _hash_inp

            self.assertEqual(_hash_inp(inp), _hash_inp(inp2))

    def test_missing_inp_yields_missing_sentinel(self) -> None:
        from agentic_swmm.agent.swmm_runtime.uncertainty_plan import _hash_inp

        self.assertEqual(_hash_inp(Path("/nonexistent/path.inp")), "missing")


@pytest.mark.skipif(not _has_salib(), reason="SALib not installed")
class SeedReproducibilityTests(unittest.TestCase):
    def test_same_seed_same_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inp = Path(tmp) / "model.inp"
            inp.write_text("[TITLE]\nx\n", encoding="utf-8")
            params = {
                "manning_n": (0.01, 0.03),
                "ksat": (1.0, 10.0),
            }
            plan_a = plan_uncertainty_run(
                inp, params, method="morris", n_samples=10, seed=123
            )
            plan_b = plan_uncertainty_run(
                inp, params, method="morris", n_samples=10, seed=123
            )
        self.assertEqual(plan_a.samples, plan_b.samples)


class ValidationTests(unittest.TestCase):
    def test_unsupported_method_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_uncertainty_run(
                Path("/tmp/none.inp"),
                {"a": (0.0, 1.0)},
                method="latin_hypercube",
            )

    def test_empty_parameters_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_uncertainty_run(Path("/tmp/none.inp"), {})

    def test_zero_n_samples_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_uncertainty_run(
                Path("/tmp/none.inp"),
                {"a": (0.0, 1.0)},
                method="morris",
                n_samples=0,
            )


@pytest.mark.skipif(not _has_salib(), reason="SALib not installed")
class CliSmokeTests(unittest.TestCase):
    def test_cli_uncertainty_plan_writes_json(self) -> None:
        from agentic_swmm.cli import build_parser

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "model.inp"
            inp.write_text("[TITLE]\nsmoke\n", encoding="utf-8")
            out = tmp_path / "plan.json"

            parser = build_parser()
            args = parser.parse_args(
                [
                    "uncertainty",
                    "plan",
                    "--base-inp",
                    str(inp),
                    "--param",
                    "manning_n=0.01,0.03",
                    "--param",
                    "ksat=1.0,10.0",
                    "--method",
                    "morris",
                    "--n-samples",
                    "10",
                    "--seed",
                    "0",
                    "--out",
                    str(out),
                ]
            )
            rc = args.func(args)
            self.assertEqual(rc, 0)
            self.assertTrue(out.is_file())
            payload = json.loads(out.read_text())
            self.assertEqual(payload["method"], "morris")
            self.assertEqual(payload["n_samples_requested"], 10)
            self.assertGreater(payload["n_samples_actual"], 0)
            self.assertEqual(
                payload["parameter_names"], ["manning_n", "ksat"]
            )

    def test_cli_malformed_param_returns_one(self) -> None:
        from agentic_swmm.cli import build_parser

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "model.inp"
            inp.write_text("[TITLE]\nx\n", encoding="utf-8")
            parser = build_parser()
            args = parser.parse_args(
                [
                    "uncertainty",
                    "plan",
                    "--base-inp",
                    str(inp),
                    "--param",
                    "missing_equals",
                ]
            )
            rc = args.func(args)
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
