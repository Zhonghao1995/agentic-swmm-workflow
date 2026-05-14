"""Corpus-build hygiene tests (PRD M6).

Verify that ``build_memory_corpus.py`` never silently emits
``case_name=None`` and that every entry carries a ``schema_version``
field. These are prerequisites for the >= 0.95 case_name ratio in
the Done Criteria.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = REPO_ROOT / "skills" / "swmm-rag-memory" / "scripts" / "build_memory_corpus.py"


def _run_build(tmp_path: Path, *, memory_dir: Path, runs_dir: Path) -> subprocess.CompletedProcess:
    out_dir = tmp_path / "rag-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--memory-dir",
            str(memory_dir),
            "--runs-dir",
            str(runs_dir),
            "--out-dir",
            str(out_dir),
            "--repo-root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )


def _write_minimal_modeling_index(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "modeling_memory_index.json").write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "records": [
                    {
                        "run_id": "case-x",
                        "case_name": "Case X",
                        "project_key": "p1",
                        "workflow_mode": "prepared_inp_cli",
                        "failure_patterns": [],
                        "model_diagnostic_ids": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_explicit_case_id_in_provenance_carries_through(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "modeling-memory"
    _write_minimal_modeling_index(memory_dir)

    runs_dir = tmp_path / "runs"
    audit_dir = runs_dir / "case-x" / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps(
            {
                "run_id": "case-x",
                "case_id": "case-x-friendly",
                "case_name": None,
                "schema_version": "1.1",
            }
        ),
        encoding="utf-8",
    )
    (audit_dir / "memory_summary.json").write_text(
        json.dumps({"run_id": "case-x", "case_name": None}),
        encoding="utf-8",
    )
    proc = _run_build(tmp_path, memory_dir=memory_dir, runs_dir=runs_dir)
    assert proc.returncode == 0, proc.stderr

    corpus_path = tmp_path / "rag-out" / "corpus.jsonl"
    lines = corpus_path.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines if line.strip()]
    # Every entry must have a non-empty case_name.
    missing = [e for e in entries if not e.get("case_name")]
    assert not missing, f"entries with empty case_name leaked through: {missing}"
    # And every entry must carry schema_version.
    no_schema = [e for e in entries if not e.get("schema_version")]
    assert not no_schema, f"entries without schema_version: {no_schema}"
    # The case-x entries should have the explicit case_id surfaced.
    case_x_entries = [e for e in entries if e.get("run_id") == "case-x"]
    assert case_x_entries
    for entry in case_x_entries:
        assert entry["case_name"] in {"Case X", "case-x-friendly", "case-x"}


def test_falls_back_to_run_dir_name_when_no_case_id(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True)
    # No modeling index records so the run-dir fallback gets exercised.

    runs_dir = tmp_path / "runs"
    audit_dir = runs_dir / "fallback-run" / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps({"run_id": "fallback-run", "schema_version": "1.1"}),
        encoding="utf-8",
    )
    (audit_dir / "memory_summary.json").write_text(
        json.dumps({"run_id": "fallback-run"}),
        encoding="utf-8",
    )

    proc = _run_build(tmp_path, memory_dir=memory_dir, runs_dir=runs_dir)
    assert proc.returncode == 0, proc.stderr

    corpus_path = tmp_path / "rag-out" / "corpus.jsonl"
    entries = [json.loads(line) for line in corpus_path.read_text().splitlines() if line.strip()]
    assert all(e.get("case_name") for e in entries)
    assert all(e.get("schema_version") for e in entries)
    # At least one entry should pick the run-dir name as the fallback.
    assert any(e.get("case_name") == "fallback-run" for e in entries)


def test_build_emits_stderr_summary_line(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory" / "modeling-memory"
    _write_minimal_modeling_index(memory_dir)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir(parents=True)

    proc = _run_build(tmp_path, memory_dir=memory_dir, runs_dir=runs_dir)
    assert proc.returncode == 0, proc.stderr
    assert "built corpus" in proc.stderr
    assert "schema=" in proc.stderr
