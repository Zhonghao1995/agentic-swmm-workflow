"""The audit reads the builder validation block by its verdict, not by truthiness.

Live test 2026-09-03 (S30): ``aiswmm run --inp`` writes
``{"status": "pass", "notes": [...]}`` and the audit's
``builder_input_validation`` check treated the truthy verdict as an error,
so every prepared-input run was audited "fail" with ``qa_failed`` in memory.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_run_builder_validation", AUDIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_prepared_input_pass_verdict_is_ok() -> None:
    audit = load_audit_module()
    block = {"status": "pass", "notes": ["Prepared-input workflow: INP copied into 05_builder."]}
    assert audit.builder_validation_ok(block) is True


def test_fail_verdict_is_not_ok_even_without_errors() -> None:
    audit = load_audit_module()
    assert audit.builder_validation_ok({"status": "fail", "notes": []}) is False


def test_legacy_error_lists_keep_their_meaning() -> None:
    audit = load_audit_module()
    assert audit.builder_validation_ok({"errors": [], "warnings": []}) is True
    assert audit.builder_validation_ok({"errors": ["missing outlet"], "warnings": []}) is False
    assert audit.builder_validation_ok({"errors": [], "notes": ["informational"]}) is True


def test_build_qa_checks_passes_a_prepared_input_run() -> None:
    audit = load_audit_module()
    qa = audit.build_qa_checks(
        acceptance_report={},
        builder_manifest={"validation": {"status": "pass", "notes": ["prepared input"]}},
        runner_manifest={"return_code": 0, "run_ok": True},
        peak_metric={"value": 3.366},
        artifacts={"runner_rpt": {"exists": True}, "runner_out": {"exists": True}},
    )
    by_id = {c["id"]: c for c in qa["checks"]}
    assert by_id["builder_input_validation"]["ok"] is True
    assert qa["fail_count"] == 0
    assert qa["status"] == "pass"
