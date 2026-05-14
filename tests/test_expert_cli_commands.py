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
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]


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
            run_dir = _seed_run(Path(tmp))
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
