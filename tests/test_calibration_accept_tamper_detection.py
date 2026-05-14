"""``aiswmm calibration accept`` refuses on patch-file tampering (#54).

The candidate-handover contract records ``candidate_inp_patch_sha256``
inside ``candidate_calibration.json`` at the moment the candidate is
written. ``aiswmm calibration accept`` recomputes the SHA of the
on-disk patch file and refuses to proceed if it does not match. This
test exercises the refusal path directly — no calibration run needed,
we hand-write a candidate + patch pair, mutate the patch on disk, and
assert the accept CLI returns non-zero with no INP change and no new
``human_decisions`` row.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"

# A minimal INP that still parses through the inp_patch selector logic.
_FIXTURE_INP = """\
[TITLE]
;;Project Title/Notes
tamper-detection fixture

[SUBCATCHMENTS]
;;Name           Raingage         Outlet         Area    %Imperv  Width   %Slope  CurbLen SnowPack
S1               RG1              J1             1858.754  25.24    8622.7   23.455   0

[SUBAREAS]
;;Subcatchment   N-Imperv N-Perv  S-Imperv S-Perv  %Zero  RouteTo        PctRouted
S1               0.0150   0.2970   0.0013  0.0030  11.1   OUTLET         100
"""

_PATCH_MAP = {
    "pct_imperv_s1": {"section": "[SUBCATCHMENTS]", "object": "S1", "field_index": 4},
    "n_imperv_s1":   {"section": "[SUBAREAS]",      "object": "S1", "field_index": 1},
}

_BEST_PARAMS = {"pct_imperv_s1": 32.0, "n_imperv_s1": 0.018}


def _aiswmm(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class CalibrationAcceptTamperDetectionTests(unittest.TestCase):
    """SHA mismatch on the patch file blocks the accept CLI."""

    def _load_cw(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "candidate_writer", SCRIPTS_DIR / "candidate_writer.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _seed_run_with_candidate(self, tmp: Path) -> tuple[Path, Path]:
        cw = self._load_cw()
        run_dir = tmp / "runs" / "case-tamper"
        audit = run_dir / "09_audit"
        audit.mkdir(parents=True)
        # Provenance must exist so the CLI doesn't bail out for missing audit.
        (audit / "experiment_provenance.json").write_text(
            json.dumps({"schema_version": "1.1", "run_id": "case-tamper"}),
            encoding="utf-8",
        )
        canonical_inp = run_dir / "model.inp"
        canonical_inp.write_text(_FIXTURE_INP, encoding="utf-8")

        cw.write_candidate_artefacts(
            run_dir=run_dir,
            canonical_inp=canonical_inp,
            patch_map=_PATCH_MAP,
            best_params=_BEST_PARAMS,
            summary={
                "primary_objective": "kge",
                "primary_value": 0.5,
                "kge_decomposition": {"r": 0.9, "alpha": 1.0, "beta": 1.0},
                "secondary_metrics": {
                    "nse": 0.4, "pbias_pct": 0.0, "rmse": 0.1,
                    "peak_error_rel": 0.1, "peak_timing_min": 0,
                },
                "strategy": "lhs",
                "iterations": 1,
                "convergence_trace_ref": None,
            },
            extra_refs={},
        )
        return run_dir, canonical_inp

    def test_patch_mutation_refused_by_accept(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir, canonical_inp = self._seed_run_with_candidate(Path(tmp))
            patch_path = run_dir / "09_audit" / "candidate_inp_patch.json"
            cand_path = run_dir / "09_audit" / "candidate_calibration.json"

            # Mutate the patch: change the `new_value` for pct_imperv_s1
            # without updating the recorded SHA in the candidate file.
            patch = json.loads(patch_path.read_text(encoding="utf-8"))
            for edit in patch["edits"]:
                if edit["param"] == "pct_imperv_s1":
                    edit["new_value"] = "99.0"
            patch_path.write_text(
                json.dumps(patch, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

            before_inp_sha = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()
            proc = _aiswmm("calibration", "accept", str(run_dir))
            after_inp_sha = hashlib.sha256(canonical_inp.read_bytes()).hexdigest()

            prov = json.loads(
                (run_dir / "09_audit" / "experiment_provenance.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertNotEqual(
            proc.returncode,
            0,
            msg=(
                "aiswmm calibration accept must refuse on patch tampering, "
                f"but it returned 0.\nSTDOUT\n{proc.stdout}\nSTDERR\n{proc.stderr}"
            ),
        )
        # INP must be left untouched.
        self.assertEqual(
            before_inp_sha,
            after_inp_sha,
            msg="canonical INP changed despite SHA mismatch refusal",
        )
        # No human_decisions appended for the refused operation.
        accepts = [
            d
            for d in (prov.get("human_decisions") or [])
            if d.get("action") == "calibration_accept"
        ]
        self.assertEqual(
            len(accepts),
            0,
            msg=(
                "accept should not record a human_decisions row on refusal, "
                f"got: {accepts}"
            ),
        )
        haystack = proc.stderr + proc.stdout
        self.assertIn("sha", haystack.lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
