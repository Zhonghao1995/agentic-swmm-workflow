"""A re-audit keeps the prior audit record as a timestamped backup.

Live test 2026-09-03 (S28): the typed audit_run tool runs the audit
script directly, and a second audit of the same run overwrote
09_audit/experiment_provenance.json with no backup, although the script's
own readers (preserved human_decisions and case_id) expect
``experiment_provenance.<utc>.json.bak`` siblings.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_run_reaudit_backup", AUDIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _minimal_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "031625_downtown-victoria-bc_run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text("[TITLE]\nvictoria\n", encoding="utf-8")
    (run_dir / "06_runner").mkdir()
    (run_dir / "06_runner" / "model.rpt").write_text("  Flow Units ............... CMS\n", encoding="utf-8")
    (run_dir / "06_runner" / "manifest.json").write_text(
        json.dumps({"return_code": 0, "files": {"rpt": str(run_dir / "06_runner" / "model.rpt")}}),
        encoding="utf-8",
    )
    return run_dir


def _audit(audit, run_dir: Path, repo_root: Path) -> None:
    argv = ["audit_run.py", "--run-dir", str(run_dir), "--repo-root", str(repo_root), "--no-obsidian"]
    with mock.patch.object(sys, "argv", argv):
        audit.main()


def test_back_up_prior_outputs_renames_only_what_exists(tmp_path: Path) -> None:
    audit = load_audit_module()
    present = tmp_path / "experiment_provenance.json"
    present.write_text("{}", encoding="utf-8")
    absent = tmp_path / "comparison.json"
    backups = audit.back_up_prior_outputs([present, absent])
    assert len(backups) == 1
    assert backups[0].name.startswith("experiment_provenance.") and backups[0].name.endswith(".json.bak")
    assert not present.exists()
    assert list(tmp_path.glob("experiment_provenance.*.json.bak")) == backups


def test_second_audit_keeps_the_first_record_as_a_backup(tmp_path: Path) -> None:
    audit = load_audit_module()
    run_dir = _minimal_run(tmp_path)
    _audit(audit, run_dir, tmp_path)
    audit_dir = run_dir / "09_audit"
    first = json.loads((audit_dir / "experiment_provenance.json").read_text(encoding="utf-8"))
    assert list(audit_dir.glob("*.bak")) == []

    _audit(audit, run_dir, tmp_path)
    backups = sorted(audit_dir.glob("experiment_provenance.*.json.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == first
    assert (audit_dir / "experiment_provenance.json").exists()
    for name in ("experiment_note", "comparison", "model_diagnostics"):
        assert list(audit_dir.glob(f"{name}.*.bak")), name
