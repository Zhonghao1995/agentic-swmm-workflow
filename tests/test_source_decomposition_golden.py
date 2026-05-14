"""Golden test for `skills/swmm-uncertainty/scripts/source_decomposition.py`.

Per issue #55, this is the integration deliverable for the
PRD-uncertainty-and-calibration-strengthening slice — it reads raw
uncertainty outputs (Sobol' indices, DREAM posterior, rainfall ensemble
summary, calibration candidate, MC propagation summary) from
``<run_dir>/09_audit/`` and emits two files:

* ``uncertainty_source_decomposition.json`` — machine-readable,
  ``schema_version == "1.0"``.
* ``uncertainty_source_summary.md`` — the paper-reviewer-facing markdown
  report with five required sections.

The golden test mocks one of each input artefact, runs the pure
function, and asserts the markdown contains the five required
section headers plus the Evidence Boundary table where every method
slot reports ``✓ ran``.

No SWMM execution is involved. The function is pure over the filesystem
state — given the same inputs it produces the same outputs.
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
        "source_decomposition_under_test", SOURCE_DECOMP_PY
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SOURCE_DECOMP_PY}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["source_decomposition_under_test"] = module
    spec.loader.exec_module(module)
    return module


def _write_complete_run(run_dir: Path) -> None:
    """Populate ``<run_dir>/09_audit/`` with one of each input artefact.

    The shapes mirror the actual contracts each prior slice ships:
    - Sobol' indices match ``skills/swmm-uncertainty/scripts/sensitivity.py``.
    - DREAM artefacts match ``test_calibrate_dream_zs_smoke.py``.
    - Rainfall summary matches the v1 schema in ``rainfall_ensemble.py``.
    - Candidate calibration matches ``candidate_writer.py``.
    """
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)

    # Sobol' (Slice 4 / #49)
    sobol = {
        "method": "sobol",
        "objective": "rmse",
        "parameters": ["pct_imperv_s1", "n_imperv_s1", "suction_s1", "ksat_s1"],
        "sample_budget": 40,
        "sobol": {"N": 4, "calc_second_order": True},
        "indices": {
            "pct_imperv_s1": {
                "S_i": 0.52,
                "S_i_conf": 0.05,
                "S_T_i": 0.70,
                "S_T_i_conf": 0.08,
            },
            "n_imperv_s1": {
                "S_i": 0.15,
                "S_i_conf": 0.04,
                "S_T_i": 0.22,
                "S_T_i_conf": 0.06,
            },
            "suction_s1": {
                "S_i": 0.04,
                "S_i_conf": 0.02,
                "S_T_i": 0.06,
                "S_T_i_conf": 0.03,
            },
            "ksat_s1": {
                "S_i": 0.02,
                "S_i_conf": 0.02,
                "S_T_i": 0.04,
                "S_T_i_conf": 0.02,
            },
        },
    }
    (audit / "sensitivity_indices.json").write_text(
        json.dumps(sobol, indent=2), encoding="utf-8"
    )

    # DREAM-ZS (Slice 2 / #53)
    posterior_csv = (
        "chain,iteration,likelihood,pct_imperv_s1,n_imperv_s1\n"
        "1,0,-0.4,30.0,0.018\n"
        "1,1,-0.35,31.0,0.019\n"
        "2,0,-0.42,29.5,0.017\n"
        "2,1,-0.37,30.5,0.018\n"
    )
    (audit / "posterior_samples.csv").write_text(posterior_csv, encoding="utf-8")
    (audit / "chain_convergence.json").write_text(
        json.dumps(
            {
                "rhat": {
                    "pct_imperv_s1": 1.05,
                    "n_imperv_s1": 1.08,
                },
                "rhat_threshold": 1.1,
                "converged": True,
                "n_chains": 2,
                "n_samples_post_burnin": 50,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    # Plot artefacts (referenced, not opened)
    (audit / "posterior_pct_imperv_s1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (audit / "posterior_correlation.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # Rainfall ensemble (Slice 5 / #51) — method A (perturbation) only
    (audit / "rainfall_ensemble_summary.json").write_text(
        json.dumps(
            {
                "schema": "swmm-uncertainty/rainfall-ensemble/v1",
                "method": "perturbation",
                "n_realisations": 50,
                "seed": 42,
                "rainfall_ensemble_stats": {
                    "peak_intensity_mm_per_hr": {
                        "p05": 19.0,
                        "p50": 23.5,
                        "p95": 28.0,
                    },
                    "total_volume_mm": {
                        "p05": 38.0,
                        "p50": 41.0,
                        "p95": 44.5,
                    },
                },
                "swmm_ensemble_stats": {
                    "peak_flow": {"p05": 0.40, "p50": 0.55, "p95": 0.72},
                    "total_volume_m3": {"p05": 110.0, "p50": 130.0, "p95": 152.0},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Candidate calibration (Slice 6 / #54 -- includes SCE-UA + DREAM strategy
    # marker in the same file, so the report can call out SCE-UA evidence as
    # present when strategy == "sce-ua").
    (audit / "candidate_calibration.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "evidence_boundary": "candidate_not_accepted_yet",
                "strategy": "sce-ua",
                "primary_objective": "kge",
                "primary_value": 0.71,
                "iterations": 200,
                "kge_decomposition": {"r": 0.85, "alpha": 0.92, "beta": 0.98},
                "secondary_metrics": {
                    "nse": 0.62,
                    "pbias_pct": -2.5,
                    "rmse": 0.18,
                    "peak_error_rel": 0.04,
                    "peak_timing_min": 5,
                },
                "best_params": {
                    "pct_imperv_s1": 30.5,
                    "n_imperv_s1": 0.018,
                },
                "candidate_inp_patch_ref": "candidate_inp_patch.json",
                "candidate_inp_patch_sha256": "deadbeef" * 8,
                "canonical_inp_ref": "model.inp",
                "canonical_inp_sha256_at_candidate_time": "cafebabe" * 8,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # MC propagation summary (Slice 0 / pre-existing)
    (audit / "uncertainty_summary.json").write_text(
        json.dumps(
            {
                "mode": "uncertainty",
                "samples": 100,
                "seed": 42,
                "node": "O1",
                "peak_cms_envelope": {
                    "p05": 0.45,
                    "p50": 0.58,
                    "p95": 0.74,
                },
                "peak_percent_change_envelope": {
                    "p05": -10.0,
                    "p50": 0.0,
                    "p95": 12.0,
                },
                "selected_plot_node": "O1",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Provenance hint (so the report can link back to it)
    (audit / "experiment_provenance.json").write_text(
        json.dumps({"schema_version": "1.2", "run_id": "golden-fixture"}),
        encoding="utf-8",
    )


class SourceDecompositionGoldenTests(unittest.TestCase):
    """Complete-run fixture exercises every method slot."""

    def setUp(self) -> None:
        self.mod = _load_module()

    def test_pure_function_emits_markdown_and_json(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-golden"
            _write_complete_run(run_dir)

            result = self.mod.decompose(run_dir=run_dir)

            audit = run_dir / "09_audit"
            md_path = audit / "uncertainty_source_summary.md"
            json_path = audit / "uncertainty_source_decomposition.json"
            self.assertTrue(md_path.is_file(), msg=f"missing {md_path}")
            self.assertTrue(json_path.is_file(), msg=f"missing {json_path}")
            self.assertEqual(result.markdown_path, md_path)
            self.assertEqual(result.json_path, json_path)

            md_text = md_path.read_text(encoding="utf-8")
            # Required headers per PRD-uncertainty template
            self.assertIn("Evidence boundary:", md_text)
            self.assertIn("## Output uncertainty envelope", md_text)
            self.assertIn("## Parameter contribution", md_text)
            self.assertIn("## Input contribution", md_text)
            self.assertIn("## Structural assumptions", md_text)
            self.assertIn("## Cross-references", md_text)
            # Evidence-boundary block lists every slot
            for slot in (
                "Sobol' SA",
                "Morris SA",
                "DREAM-ZS",
                "SCE-UA",
                "Rainfall ensemble",
                "MC propagation",
            ):
                self.assertIn(slot, md_text, msg=f"slot missing in MD: {slot}")
            # Complete fixture: Sobol present, Morris absent.
            self.assertIn("Sobol' SA       : ✓", md_text)
            self.assertIn("Morris SA       : ✗", md_text)
            self.assertIn("DREAM-ZS        : ✓", md_text)
            self.assertIn("SCE-UA          : ✓", md_text)
            # Method A only (no method B / IDF) — phrasing from issue #55
            self.assertIn(
                "Rainfall ensemble: ✓ method A only (method B not run)", md_text
            )
            self.assertIn("MC propagation  : ✓", md_text)

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "1.0")
            # JSON must mirror the evidence boundary so downstream
            # tooling can flag partial runs without re-reading MD.
            evidence = payload["evidence_boundary"]
            self.assertTrue(evidence["sobol"]["ran"])
            self.assertFalse(evidence["morris"]["ran"])
            self.assertTrue(evidence["dream_zs"]["ran"])
            self.assertTrue(evidence["sce_ua"]["ran"])
            self.assertTrue(evidence["rainfall_ensemble"]["ran"])
            self.assertTrue(evidence["mc_propagation"]["ran"])
            # Parameter contribution rows must be sorted by S_T_i descending
            ranking = payload["parameter_contribution"]["sobol_total_effect_sorted"]
            self.assertEqual(ranking[0]["parameter"], "pct_imperv_s1")
            self.assertGreater(ranking[0]["S_T_i"], ranking[-1]["S_T_i"])
            # Cross-references resolve to relative paths under 09_audit/
            refs = payload["cross_references"]
            self.assertEqual(refs["sensitivity_indices"], "09_audit/sensitivity_indices.json")
            self.assertEqual(refs["posterior_samples"], "09_audit/posterior_samples.csv")
            self.assertEqual(refs["rainfall_ensemble_summary"], "09_audit/rainfall_ensemble_summary.json")

    def test_re_invocation_overwrites_idempotently(self) -> None:
        """Calling decompose twice on the same dir yields the same files."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "case-golden2"
            _write_complete_run(run_dir)
            r1 = self.mod.decompose(run_dir=run_dir)
            text1 = r1.markdown_path.read_text(encoding="utf-8")
            payload1 = json.loads(r1.json_path.read_text(encoding="utf-8"))
            payload1.pop("generated_at_utc", None)

            self.mod.decompose(run_dir=run_dir)
            text2 = r1.markdown_path.read_text(encoding="utf-8")
            payload2 = json.loads(r1.json_path.read_text(encoding="utf-8"))
            payload2.pop("generated_at_utc", None)
            self.assertEqual(text1, text2)
            self.assertEqual(payload1, payload2)


if __name__ == "__main__":
    unittest.main()
