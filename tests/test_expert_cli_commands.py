"""Positive tests for the four expert-only CLI subcommands (PRD-Z).

Each subcommand:

* ``aiswmm calibration accept <run_dir>``
* ``aiswmm pour_point confirm <case_id> [--run-dir <run_dir>]``
* ``aiswmm thresholds override <run_dir> <name> <value>``
* ``aiswmm publish <run_dir>``

invokes through the existing ``agentic_swmm.cli`` entry point and must
append a ``human_decisions`` record to the run's
``09_audit/experiment_provenance.json``. The tests run the CLI as a
subprocess so the argparse wiring is exercised end-to-end.

``aiswmm publish`` additionally refuses if no provenance file exists
for the run.

``aiswmm calibration accept`` (issue #54) refuses if there is no
``candidate_calibration.json`` in the run's 09_audit/.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"


# Minimal INP fixture that the candidate writer's patch selectors can hit;
# avoids depending on the full Tod Creek demo for a pure CLI test.
_CANDIDATE_FIXTURE_INP = """\
[TITLE]
;;Project Title/Notes
calibration accept fixture

[SUBCATCHMENTS]
;;Name           Raingage         Outlet         Area    %Imperv  Width   %Slope  CurbLen SnowPack
S1               RG1              J1             1858.754  25.24    8622.7   23.455   0

[SUBAREAS]
;;Subcatchment   N-Imperv N-Perv  S-Imperv S-Perv  %Zero  RouteTo        PctRouted
S1               0.0150   0.2970   0.0013  0.0030  11.1   OUTLET         100
"""

_CANDIDATE_FIXTURE_PATCH_MAP = {
    "pct_imperv_s1": {"section": "[SUBCATCHMENTS]", "object": "S1", "field_index": 4},
    "n_imperv_s1":   {"section": "[SUBAREAS]",      "object": "S1", "field_index": 1},
}

_CANDIDATE_FIXTURE_PARAMS = {"pct_imperv_s1": 32.0, "n_imperv_s1": 0.018}


def _seed_run(tmp: Path, with_provenance: bool = True) -> Path:
    run_dir = tmp / "runs" / "case-a"
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True)
    if with_provenance:
        (audit / "experiment_provenance.json").write_text(
            json.dumps({"schema_version": "1.1", "run_id": "case-a"}),
            encoding="utf-8",
        )
    return run_dir


def _load_candidate_writer():
    spec = importlib.util.spec_from_file_location(
        "candidate_writer", SCRIPTS_DIR / "candidate_writer.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_run_with_candidate(tmp: Path) -> Path:
    """Seed a run dir with provenance + the 3 candidate-handover artefacts."""

    run_dir = _seed_run(tmp)
    canonical_inp = run_dir / "model.inp"
    canonical_inp.write_text(_CANDIDATE_FIXTURE_INP, encoding="utf-8")
    cw = _load_candidate_writer()
    cw.write_candidate_artefacts(
        run_dir=run_dir,
        canonical_inp=canonical_inp,
        patch_map=_CANDIDATE_FIXTURE_PATCH_MAP,
        best_params=_CANDIDATE_FIXTURE_PARAMS,
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
    return run_dir


def _aiswmm(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "agentic_swmm.cli", *args],
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
    )


def _decisions(run_dir: Path) -> list[dict]:
    prov = json.loads(
        (run_dir / "09_audit" / "experiment_provenance.json").read_text(
            encoding="utf-8"
        )
    )
    return prov.get("human_decisions") or []


class CalibrationAcceptTests(unittest.TestCase):
    def test_calibration_accept_records_human_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_with_candidate(Path(tmp))
            proc = _aiswmm("calibration", "accept", str(run_dir))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            decisions = _decisions(run_dir)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "calibration_accept")


class PourPointConfirmTests(unittest.TestCase):
    def test_pour_point_confirm_records_human_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp))
            proc = _aiswmm(
                "pour_point",
                "confirm",
                "case-a",
                "--run-dir",
                str(run_dir),
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            decisions = _decisions(run_dir)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "pour_point_confirm")


class ThresholdsOverrideTests(unittest.TestCase):
    def test_thresholds_override_records_human_decision_with_value(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp))
            proc = _aiswmm(
                "thresholds",
                "override",
                str(run_dir),
                "continuity_error_over_threshold",
                "8.0",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            decisions = _decisions(run_dir)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "thresholds_override")
        self.assertIn("8.0", decisions[0]["decision_text"])
        self.assertIn("continuity_error_over_threshold", decisions[0]["decision_text"])


class PublishTests(unittest.TestCase):
    def test_publish_records_human_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp))
            proc = _aiswmm("publish", str(run_dir))
            self.assertEqual(proc.returncode, 0, proc.stderr)
            decisions = _decisions(run_dir)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["action"], "publish")

    def test_publish_refuses_when_provenance_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run(Path(tmp), with_provenance=False)
            # Remove the seeded directory so the provenance is clearly absent.
            proc = _aiswmm("publish", str(run_dir))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("experiment_provenance.json", proc.stderr + proc.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
