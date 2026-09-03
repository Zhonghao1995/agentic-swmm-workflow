"""The audit finds the built INP by layout when no manifest records it.

Live test 2026-09-03 (S27): every Canada-route run carries
``05_builder/model.inp`` and no builder or top manifest, and the audit
called that model missing evidence. The false flag flowed into
memory_summary and failure_advice on a passing run.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "skills" / "swmm-experiment-audit" / "scripts" / "audit_run.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_run_canonical_inp", AUDIT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_find_builder_inp_prefers_canonical_model_inp(tmp_path: Path) -> None:
    audit = load_audit_module()
    (tmp_path / "05_builder").mkdir()
    (tmp_path / "05_builder" / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
    (tmp_path / "05_builder" / "other.inp").write_text("[TITLE]\n", encoding="utf-8")
    assert audit.find_builder_inp(tmp_path) == tmp_path / "05_builder" / "model.inp"


def test_find_builder_inp_accepts_a_lone_legacy_inp(tmp_path: Path) -> None:
    audit = load_audit_module()
    (tmp_path / "04_builder").mkdir()
    (tmp_path / "04_builder" / "case.inp").write_text("[TITLE]\n", encoding="utf-8")
    assert audit.find_builder_inp(tmp_path) == tmp_path / "04_builder" / "case.inp"


def test_find_builder_inp_stays_silent_when_ambiguous_or_absent(tmp_path: Path) -> None:
    audit = load_audit_module()
    assert audit.find_builder_inp(tmp_path) is None
    (tmp_path / "05_builder").mkdir()
    (tmp_path / "05_builder" / "a.inp").write_text("", encoding="utf-8")
    (tmp_path / "05_builder" / "b.inp").write_text("", encoding="utf-8")
    assert audit.find_builder_inp(tmp_path) is None


def test_collect_run_marks_the_canonical_inp_present_without_a_manifest(tmp_path: Path) -> None:
    audit = load_audit_module()
    run_dir = tmp_path / "024635_downtown-victoria-bc_run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "05_builder" / "model.inp").write_text("[TITLE]\nvictoria\n", encoding="utf-8")
    (run_dir / "06_runner").mkdir()
    (run_dir / "06_runner" / "model.rpt").write_text("  Flow Units ............... CMS\n", encoding="utf-8")
    (run_dir / "06_runner" / "manifest.json").write_text(
        json.dumps({"return_code": 0, "files": {"rpt": str(run_dir / "06_runner" / "model.rpt")}}),
        encoding="utf-8",
    )
    record, _ = audit.collect_run(run_dir, repo_root=tmp_path)
    text = json.dumps(record)
    assert '"model_inp"' in text
    artifacts = record.get("artifacts") or {}
    model_inp = artifacts.get("model_inp") if isinstance(artifacts, dict) else None
    assert model_inp is not None, list(record.keys())
    assert model_inp.get("exists") is True
    assert str(model_inp.get("absolute_path", "")).endswith("05_builder/model.inp")
