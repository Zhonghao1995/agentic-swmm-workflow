"""Unit tests for ``candidate_writer`` (issue #54).

The candidate-handover contract emits three artefacts to ``09_audit/``
when calibration finishes:

* ``candidate_calibration.json`` — best params + metrics + KGE
  decomposition + secondary metrics + ``evidence_boundary ==
  "candidate_not_accepted_yet"`` + SHA of the INP patch + (DREAM only)
  reference to posterior samples.
* ``candidate_inp_patch.json`` — diff to apply at accept-time. One row
  per parameter, with ``section``, ``object``, ``field_index``,
  ``old_value`` and ``new_value`` so an auditor (and ``aiswmm
  calibration accept``) can verify exactly what will change on the
  canonical INP.
* ``calibration_report.md`` — human-readable, contains a KGE
  decomposition table, the secondary-metrics table, a strategy line,
  and convergence/posterior references when applicable.

These tests are pure unit tests over the writer module — no SWMM, no
spotpy. They drive the API contract; the green implementation will land
in ``skills/swmm-calibration/scripts/candidate_writer.py`` next.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
CANDIDATE_WRITER = SCRIPTS_DIR / "candidate_writer.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Fixture INP — one line per data row in each section keeps the patch
# diff simple to reason about. Mirrors the Tod Creek single-subcatchment
# demo used elsewhere in the suite.
_FIXTURE_INP = """\
[TITLE]
;;Project Title/Notes
candidate_writer fixture

[SUBCATCHMENTS]
;;Name           Raingage         Outlet         Area    %Imperv  Width   %Slope  CurbLen SnowPack
S1               RG1              J1             1858.754  25.24    8622.7   23.455   0

[SUBAREAS]
;;Subcatchment   N-Imperv N-Perv  S-Imperv S-Perv  %Zero  RouteTo        PctRouted
S1               0.0150   0.2970   0.0013  0.0030  11.1   OUTLET         100

[INFILTRATION]
;;Subcatchment   Suction  Ksat    IMDmax
S1               90.0     8.0     0.25
"""


_FIXTURE_PATCH_MAP = {
    "pct_imperv_s1": {"section": "[SUBCATCHMENTS]", "object": "S1", "field_index": 4},
    "n_imperv_s1":   {"section": "[SUBAREAS]",      "object": "S1", "field_index": 1},
    "suction_s1":    {"section": "[INFILTRATION]",  "object": "S1", "field_index": 1},
}


# Slice 1 / Slice 5 summary shape, populated for SCE-UA.
def _sceua_summary() -> dict:
    return {
        "primary_objective": "kge",
        "primary_value": 0.81,
        "kge_decomposition": {"r": 0.93, "alpha": 1.04, "beta": 0.98},
        "secondary_metrics": {
            "nse": 0.75,
            "pbias_pct": -3.2,
            "rmse": 0.041,
            "peak_error_rel": 0.07,
            "peak_timing_min": 10,
        },
        "strategy": "sceua",
        "iterations": 200,
        "convergence_trace_ref": "convergence.csv",
    }


def _dream_summary() -> dict:
    base = _sceua_summary()
    base["strategy"] = "dream-zs"
    base["primary_value"] = 0.83
    base["convergence_trace_ref"] = "chain_convergence.json"
    base["posterior_summary"] = {
        "n_chains": 4,
        "n_chains_requested": 4,
        "n_samples_post_burnin": 1996,
        "converged": True,
        "rhat_threshold": 1.2,
        "rhat": {"pct_imperv_s1": 1.07, "n_imperv_s1": 1.04, "suction_s1": 1.03},
        "posterior_csv_ref": "posterior_samples.csv",
        "correlation_png_ref": "posterior_correlation.png",
        "per_parameter": {
            "pct_imperv_s1": {"mean": 29.7, "median": 29.8, "std": 1.2, "q05": 27.6, "q95": 31.5},
            "n_imperv_s1": {"mean": 0.018, "median": 0.0179, "std": 0.001, "q05": 0.016, "q95": 0.020},
            "suction_s1": {"mean": 93.0, "median": 93.0, "std": 2.0, "q05": 89.0, "q95": 96.0},
        },
    }
    return base


_BEST_PARAMS = {"pct_imperv_s1": 32.0, "n_imperv_s1": 0.018, "suction_s1": 95.0}


class CandidateWriterTests(unittest.TestCase):
    """Behavioural tests for the three-artefact emit."""

    def setUp(self) -> None:
        self.assertTrue(
            CANDIDATE_WRITER.exists(),
            msg=(
                "Expected candidate_writer.py at "
                f"{CANDIDATE_WRITER}; this test was written first to drive its API."
            ),
        )
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        self.cw = _load_module("candidate_writer", CANDIDATE_WRITER)

    # ------------------------------------------------------------------ helpers

    def _seed_run(self, tmp: Path) -> tuple[Path, Path]:
        run_dir = tmp / "runs" / "case-a"
        audit = run_dir / "09_audit"
        audit.mkdir(parents=True)
        canonical_inp = run_dir / "model.inp"
        canonical_inp.write_text(_FIXTURE_INP, encoding="utf-8")
        return run_dir, canonical_inp

    # -------------------------------------------------------------- patch diff

    def test_build_inp_patch_extracts_old_values_and_records_new(self) -> None:
        diff = self.cw.build_inp_patch(_FIXTURE_INP, _FIXTURE_PATCH_MAP, _BEST_PARAMS)
        self.assertEqual(diff["schema_version"], "1.0")
        edits = {edit["param"]: edit for edit in diff["edits"]}
        self.assertEqual(set(edits), set(_BEST_PARAMS))
        # SUBCATCHMENTS S1, field 4 is %Imperv -> 25.24
        self.assertEqual(edits["pct_imperv_s1"]["section"], "[SUBCATCHMENTS]")
        self.assertEqual(edits["pct_imperv_s1"]["object"], "S1")
        self.assertEqual(edits["pct_imperv_s1"]["field_index"], 4)
        self.assertEqual(edits["pct_imperv_s1"]["old_value"], "25.24")
        self.assertEqual(edits["pct_imperv_s1"]["new_value"], "32.0")
        # SUBAREAS S1, field 1 = N-Imperv -> 0.0150 -> 0.018
        self.assertEqual(edits["n_imperv_s1"]["old_value"], "0.0150")
        self.assertEqual(edits["n_imperv_s1"]["new_value"], "0.018")
        # INFILTRATION S1, field 1 = Suction -> 90.0 -> 95.0
        self.assertEqual(edits["suction_s1"]["old_value"], "90.0")
        self.assertEqual(edits["suction_s1"]["new_value"], "95.0")

    def test_build_inp_patch_raises_on_missing_patch_map_key(self) -> None:
        bad_params = dict(_BEST_PARAMS)
        bad_params["not_in_patch_map"] = 1.0
        with self.assertRaises(KeyError):
            self.cw.build_inp_patch(_FIXTURE_INP, _FIXTURE_PATCH_MAP, bad_params)

    # ---------------------------------------------------------- sha computation

    def test_sha256_of_canonical_json_is_stable(self) -> None:
        payload = {"edits": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]}
        sha_a = self.cw.sha256_of_canonical_json(payload)
        sha_b = self.cw.sha256_of_canonical_json(dict(payload))
        self.assertEqual(sha_a, sha_b)
        self.assertEqual(len(sha_a), 64)  # hex digest

        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, indent=2).encode("utf-8")
        ).hexdigest()
        self.assertEqual(sha_a, expected)

    # ----------------------------------------------------- end-to-end sce-ua write

    def test_write_candidate_artefacts_sceua_writes_three_files(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir, canonical_inp = self._seed_run(Path(tmp))
            result = self.cw.write_candidate_artefacts(
                run_dir=run_dir,
                canonical_inp=canonical_inp,
                patch_map=_FIXTURE_PATCH_MAP,
                best_params=_BEST_PARAMS,
                summary=_sceua_summary(),
                extra_refs={"convergence_csv": "convergence.csv"},
            )
            audit = run_dir / "09_audit"
            cand_json = audit / "candidate_calibration.json"
            patch_json = audit / "candidate_inp_patch.json"
            report_md = audit / "calibration_report.md"

            self.assertTrue(cand_json.exists())
            self.assertTrue(patch_json.exists())
            self.assertTrue(report_md.exists())

            cand = json.loads(cand_json.read_text(encoding="utf-8"))
            patch = json.loads(patch_json.read_text(encoding="utf-8"))
            report_text = report_md.read_text(encoding="utf-8")

        # Hand-over fields.
        self.assertEqual(cand["evidence_boundary"], "candidate_not_accepted_yet")
        self.assertEqual(cand["strategy"], "sceua")
        self.assertEqual(cand["best_params"], _BEST_PARAMS)
        self.assertEqual(cand["primary_objective"], "kge")
        self.assertAlmostEqual(cand["primary_value"], 0.81)
        self.assertEqual(cand["kge_decomposition"], _sceua_summary()["kge_decomposition"])
        self.assertEqual(cand["secondary_metrics"], _sceua_summary()["secondary_metrics"])
        self.assertEqual(cand["candidate_inp_patch_ref"], "candidate_inp_patch.json")
        # Tamper-detection seam — SHA must match the on-disk patch file
        recomputed = self.cw.sha256_of_canonical_json(patch)
        self.assertEqual(cand["candidate_inp_patch_sha256"], recomputed)

        # Report contains KGE decomposition + convergence reference (SCE-UA).
        self.assertIn("KGE", report_text)
        self.assertIn("convergence.csv", report_text)
        self.assertIn("0.93", report_text)  # r in KGE decomposition
        self.assertIn("Candidate not accepted yet", report_text)
        # Returned summary dict mirrors candidate file for callers.
        self.assertEqual(result["candidate_path"], str(cand_json))
        self.assertEqual(result["patch_path"], str(patch_json))
        self.assertEqual(result["report_path"], str(report_md))

    # ----------------------------------------------------- end-to-end dream write

    def test_write_candidate_artefacts_dream_includes_posterior_ref(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir, canonical_inp = self._seed_run(Path(tmp))
            self.cw.write_candidate_artefacts(
                run_dir=run_dir,
                canonical_inp=canonical_inp,
                patch_map=_FIXTURE_PATCH_MAP,
                best_params=_BEST_PARAMS,
                summary=_dream_summary(),
                extra_refs={
                    "convergence_csv": "chain_convergence.json",
                    "posterior_samples_csv": "posterior_samples.csv",
                    "posterior_correlation_png": "posterior_correlation.png",
                },
            )
            audit = run_dir / "09_audit"
            cand = json.loads((audit / "candidate_calibration.json").read_text(encoding="utf-8"))
            report_text = (audit / "calibration_report.md").read_text(encoding="utf-8")

        self.assertEqual(cand["strategy"], "dream-zs")
        # DREAM-only: posterior_samples_ref recorded in the candidate file
        self.assertEqual(cand["posterior_samples_ref"], "posterior_samples.csv")
        self.assertIn("posterior_summary", cand)
        # Report cites posterior plots.
        self.assertIn("posterior_samples.csv", report_text)
        self.assertIn("posterior_correlation.png", report_text)

    # -------------------------------------------------- non-overwrite vs evidence

    def test_canonical_inp_is_not_modified_by_write_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir, canonical_inp = self._seed_run(Path(tmp))
            before = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()
            self.cw.write_candidate_artefacts(
                run_dir=run_dir,
                canonical_inp=canonical_inp,
                patch_map=_FIXTURE_PATCH_MAP,
                best_params=_BEST_PARAMS,
                summary=_sceua_summary(),
                extra_refs={"convergence_csv": "convergence.csv"},
            )
            after = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()
        self.assertEqual(before, after, msg="write_candidate_artefacts must not touch the canonical INP")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
