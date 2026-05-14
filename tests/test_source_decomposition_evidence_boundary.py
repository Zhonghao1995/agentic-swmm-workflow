"""Evidence-boundary tests for source_decomposition (#55).

Partial fixtures: each test seeds a run dir that contains only a subset
of the raw uncertainty outputs and asserts the generated Evidence
Boundary header reflects exactly that subset. The critical contract is
that NO method is ever silently absent from the table — every potential
slot prints either ``✓`` or ``✗``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DECOMP_PY = (
    REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts" / "source_decomposition.py"
)


def _load_module():
    if not SOURCE_DECOMP_PY.exists():
        raise unittest.SkipTest(
            "source_decomposition.py not present yet; this test guards #55."
        )
    spec = importlib.util.spec_from_file_location(
        "source_decomposition_under_test_eb", SOURCE_DECOMP_PY
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SOURCE_DECOMP_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["source_decomposition_under_test_eb"] = module
    spec.loader.exec_module(module)
    return module


def _write_morris_only(run_dir: Path) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "sensitivity_indices.json").write_text(
        json.dumps(
            {
                "method": "morris",
                "objective": "rmse",
                "parameters": ["pct_imperv_s1", "n_imperv_s1"],
                "sample_budget": 6,
                "morris": {"r": 2, "num_levels": 4},
                "indices": {
                    "pct_imperv_s1": {
                        "mu": 0.10,
                        "mu_star": 0.40,
                        "sigma": 0.15,
                        "mu_star_conf": 0.05,
                    },
                    "n_imperv_s1": {
                        "mu": -0.02,
                        "mu_star": 0.12,
                        "sigma": 0.04,
                        "mu_star_conf": 0.03,
                    },
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_dream_only(run_dir: Path) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    posterior_csv = (
        "chain,iteration,likelihood,pct_imperv_s1\n"
        "1,0,-0.4,30.0\n"
        "1,1,-0.35,31.0\n"
    )
    (audit / "posterior_samples.csv").write_text(posterior_csv, encoding="utf-8")
    (audit / "chain_convergence.json").write_text(
        json.dumps({"rhat": {"pct_imperv_s1": 1.05}, "converged": True}),
        encoding="utf-8",
    )


def _write_rainfall_only(run_dir: Path) -> None:
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "rainfall_ensemble_summary.json").write_text(
        json.dumps(
            {
                "schema": "swmm-uncertainty/rainfall-ensemble/v1",
                "method": "idf",
                "n_realisations": 100,
                "seed": 42,
                "rainfall_ensemble_stats": {
                    "peak_intensity_mm_per_hr": {"p05": 18, "p50": 24, "p95": 30},
                    "total_volume_mm": {"p05": 35, "p50": 42, "p95": 49},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )


class EvidenceBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_module()

    def _run(self, run_dir: Path) -> dict:
        result = self.mod.decompose(run_dir=run_dir)
        payload = json.loads(result.json_path.read_text(encoding="utf-8"))
        md = result.markdown_path.read_text(encoding="utf-8")
        return {"payload": payload, "md": md, "result": result}

    def test_morris_only_marks_sobol_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-morris-only"
            _write_morris_only(run_dir)
            r = self._run(run_dir)
            eb = r["payload"]["evidence_boundary"]
            self.assertTrue(eb["morris"]["ran"])
            self.assertFalse(eb["sobol"]["ran"])
            self.assertFalse(eb["dream_zs"]["ran"])
            self.assertFalse(eb["sce_ua"]["ran"])
            self.assertFalse(eb["rainfall_ensemble"]["ran"])
            self.assertFalse(eb["mc_propagation"]["ran"])
            self.assertIn("Morris SA       : ✓", r["md"])
            self.assertIn("Sobol' SA       : ✗", r["md"])
            self.assertIn("DREAM-ZS        : ✗", r["md"])
            self.assertIn("SCE-UA          : ✗", r["md"])
            # Method-A/B sub-flag: rainfall absent overall, both sub-methods
            # also reported absent
            self.assertIn("Rainfall ensemble: ✗", r["md"])

    def test_dream_only_marks_other_methods_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-dream-only"
            _write_dream_only(run_dir)
            r = self._run(run_dir)
            eb = r["payload"]["evidence_boundary"]
            self.assertFalse(eb["sobol"]["ran"])
            self.assertFalse(eb["morris"]["ran"])
            self.assertTrue(eb["dream_zs"]["ran"])
            self.assertFalse(eb["sce_ua"]["ran"])
            self.assertFalse(eb["rainfall_ensemble"]["ran"])
            self.assertIn("DREAM-ZS        : ✓", r["md"])
            self.assertIn("SCE-UA          : ✗", r["md"])

    def test_rainfall_only_reports_method_b_for_idf(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-rainfall-only"
            _write_rainfall_only(run_dir)
            r = self._run(run_dir)
            eb = r["payload"]["evidence_boundary"]
            self.assertTrue(eb["rainfall_ensemble"]["ran"])
            # Sub-method breakdown: IDF only means method B is present, A is
            # absent.
            self.assertEqual(eb["rainfall_ensemble"]["method"], "idf")
            self.assertFalse(eb["sobol"]["ran"])
            self.assertFalse(eb["dream_zs"]["ran"])
            self.assertIn("Rainfall ensemble: ✓ method B", r["md"])

    def test_empty_run_marks_every_slot_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-empty"
            (run_dir / "09_audit").mkdir(parents=True)
            # No raw outputs at all — but the function must NOT raise; the
            # CLI is the layer that turns this into a non-zero exit.
            r = self._run(run_dir)
            eb = r["payload"]["evidence_boundary"]
            for slot in ("sobol", "morris", "dream_zs", "sce_ua", "rainfall_ensemble", "mc_propagation"):
                self.assertFalse(eb[slot]["ran"], msg=f"slot {slot} should be absent")
            # Boundary table still rendered for every slot.
            for label in (
                "Sobol' SA       : ✗",
                "Morris SA       : ✗",
                "DREAM-ZS        : ✗",
                "SCE-UA          : ✗",
                "Rainfall ensemble: ✗",
                "MC propagation  : ✗",
            ):
                self.assertIn(label, r["md"])


if __name__ == "__main__":
    unittest.main()
