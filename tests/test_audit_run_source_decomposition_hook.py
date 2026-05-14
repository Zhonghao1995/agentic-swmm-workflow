"""Audit hook test for source_decomposition (#55).

When ``audit_run.py`` runs against a run directory that already
contains at least one uncertainty raw artefact under ``09_audit/``,
the script must automatically (re)generate
``uncertainty_source_summary.md`` and
``uncertainty_source_decomposition.json`` so the modeller does not
have to call ``aiswmm uncertainty source`` separately. When no
uncertainty raw artefacts exist, the hook is a no-op and the audit
run is unchanged.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PY = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def _seed_run_with_uncertainty(tmp: Path) -> Path:
    """Make a minimal run dir + audit folder with one Sobol artefact.

    No manifest.json / acceptance_report — the audit script tolerates
    that and still produces an experiment_provenance.json + note.
    """
    run_dir = tmp / "case-audit-hook"
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True, exist_ok=True)
    (audit / "sensitivity_indices.json").write_text(
        json.dumps(
            {
                "method": "sobol",
                "parameters": ["p1"],
                "sample_budget": 6,
                "indices": {
                    "p1": {"S_i": 0.5, "S_i_conf": 0.05, "S_T_i": 0.7, "S_T_i_conf": 0.05},
                },
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "case-audit-hook"}),
        encoding="utf-8",
    )
    return run_dir


def _seed_run_without_uncertainty(tmp: Path) -> Path:
    run_dir = tmp / "case-audit-no-uncertainty"
    (run_dir / "09_audit").mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "case-audit-no-uncertainty"}),
        encoding="utf-8",
    )
    return run_dir


class AuditRunSourceDecompositionHookTests(unittest.TestCase):
    def test_audit_run_generates_source_summary_when_outputs_present(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_with_uncertainty(Path(tmp))
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_PY),
                    "--run-dir",
                    str(run_dir),
                    "--no-obsidian",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                proc.returncode,
                0,
                msg=f"audit_run failed: stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )
            audit = run_dir / "09_audit"
            self.assertTrue(
                (audit / "uncertainty_source_summary.md").is_file(),
                msg="audit-end hook did not generate uncertainty_source_summary.md",
            )
            self.assertTrue(
                (audit / "uncertainty_source_decomposition.json").is_file(),
                msg="audit-end hook did not generate uncertainty_source_decomposition.json",
            )

    def test_audit_run_skips_hook_when_no_uncertainty_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _seed_run_without_uncertainty(Path(tmp))
            proc = subprocess.run(
                [
                    sys.executable,
                    str(AUDIT_PY),
                    "--run-dir",
                    str(run_dir),
                    "--no-obsidian",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0)
            audit = run_dir / "09_audit"
            # Hook is a no-op — the audit must NOT manufacture an empty
            # summary when there's nothing to report.
            self.assertFalse(
                (audit / "uncertainty_source_summary.md").is_file(),
                msg="audit-end hook generated source summary when no raw outputs were present",
            )


if __name__ == "__main__":
    unittest.main()
