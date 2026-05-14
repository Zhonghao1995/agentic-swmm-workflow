"""Tests for the M2 audit -> memory auto-trigger hook.

These tests exercise ``agentic_swmm/commands/audit.py:main`` against a
fixture run directory and assert that:

- the default invocation triggers memory summarisation + RAG refresh
  (both files mtime-bumped),
- ``--no-memory`` leaves both memory directories untouched,
- ``--no-rag`` updates lessons_learned.md but leaves corpus.jsonl alone,
- the skip-memory heuristic catches acceptance / agent / ci-tagged
  runs and writes a one-line .skip_log.jsonl entry.

The audit subprocess that drives swmm-experiment-audit is patched
out so the tests run in pure Python and stay deterministic.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest


def _make_run_dir(tmp_path: Path, *, category: str | None = None) -> Path:
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "case-mem"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    provenance: dict[str, Any] = {
        "run_id": "case-mem",
        "case_name": "Case Mem",
        "schema_version": "1.1",
    }
    if category:
        provenance["category"] = category
    (audit_dir / "experiment_provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    (audit_dir / "experiment_note.md").write_text("# note\n", encoding="utf-8")
    return run_dir


def _make_memory_dirs(tmp_path: Path) -> tuple[Path, Path]:
    mem = tmp_path / "memory" / "modeling-memory"
    mem.mkdir(parents=True)
    lessons = mem / "lessons_learned.md"
    lessons.write_text("<!-- schema_version: 1.1 -->\n# Lessons\n", encoding="utf-8")

    rag = tmp_path / "memory" / "rag-memory"
    rag.mkdir(parents=True)
    corpus = rag / "corpus.jsonl"
    corpus.write_text("", encoding="utf-8")
    return lessons, corpus


def _stub_audit_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real audit subprocess with a no-op that returns success."""
    from agentic_swmm.commands import audit as audit_cmd

    class _Result:
        def __init__(self) -> None:
            self.return_code = 0
            self.stdout = "{}"
            self.stderr = ""

    def _fake_run_command(*args, **kwargs):  # type: ignore[no-untyped-def]
        return _Result()

    monkeypatch.setattr(audit_cmd, "run_command", _fake_run_command)
    monkeypatch.setattr(audit_cmd, "append_trace", lambda *a, **k: None)


def _invoke_audit(args_ns: argparse.Namespace) -> int:
    from agentic_swmm.commands.audit import main

    return main(args_ns)


def test_default_audit_triggers_memory_and_rag_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audit_subprocess(monkeypatch)
    run_dir = _make_run_dir(tmp_path)
    lessons, corpus = _make_memory_dirs(tmp_path)

    # Pin mtimes far in the past so we can detect updates.
    past = time.time() - 3600
    os.utime(lessons, (past, past))
    os.utime(corpus, (past, past))

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(corpus.parent))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(lessons.parent))

    args = argparse.Namespace(
        run_dir=run_dir,
        compare_to=None,
        case_name=None,
        workflow_mode=None,
        objective=None,
        obsidian=False,
        no_memory=False,
        no_rag=False,
        rebuild=False,
    )
    rc = _invoke_audit(args)
    assert rc == 0

    assert lessons.stat().st_mtime > past + 1, "lessons_learned.md should be refreshed"
    assert corpus.stat().st_mtime > past + 1, "corpus.jsonl should be refreshed"


def test_no_memory_flag_leaves_memory_files_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audit_subprocess(monkeypatch)
    run_dir = _make_run_dir(tmp_path)
    lessons, corpus = _make_memory_dirs(tmp_path)

    past = time.time() - 3600
    os.utime(lessons, (past, past))
    os.utime(corpus, (past, past))

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(corpus.parent))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(lessons.parent))

    args = argparse.Namespace(
        run_dir=run_dir,
        compare_to=None,
        case_name=None,
        workflow_mode=None,
        objective=None,
        obsidian=False,
        no_memory=True,
        no_rag=False,
        rebuild=False,
    )
    rc = _invoke_audit(args)
    assert rc == 0

    assert abs(lessons.stat().st_mtime - past) < 1.0
    assert abs(corpus.stat().st_mtime - past) < 1.0


def test_no_rag_flag_updates_lessons_but_leaves_corpus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audit_subprocess(monkeypatch)
    run_dir = _make_run_dir(tmp_path)
    lessons, corpus = _make_memory_dirs(tmp_path)

    past = time.time() - 3600
    os.utime(lessons, (past, past))
    os.utime(corpus, (past, past))

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(corpus.parent))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(lessons.parent))

    args = argparse.Namespace(
        run_dir=run_dir,
        compare_to=None,
        case_name=None,
        workflow_mode=None,
        objective=None,
        obsidian=False,
        no_memory=False,
        no_rag=True,
        rebuild=False,
    )
    rc = _invoke_audit(args)
    assert rc == 0

    assert lessons.stat().st_mtime > past + 1
    assert abs(corpus.stat().st_mtime - past) < 1.0


def test_skip_memory_run_under_acceptance_logs_to_skip_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_audit_subprocess(monkeypatch)
    # Place the run under runs/acceptance/...
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "acceptance" / "case-skip"
    audit_dir = run_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps({"run_id": "case-skip", "case_name": "Skip", "schema_version": "1.1"}),
        encoding="utf-8",
    )

    lessons, corpus = _make_memory_dirs(tmp_path)
    past = time.time() - 3600
    os.utime(lessons, (past, past))
    os.utime(corpus, (past, past))

    monkeypatch.setenv("AISWMM_LESSONS_PATH", str(lessons))
    monkeypatch.setenv("AISWMM_RAG_DIR", str(corpus.parent))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_dir))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(lessons.parent))

    args = argparse.Namespace(
        run_dir=run_dir,
        compare_to=None,
        case_name=None,
        workflow_mode=None,
        objective=None,
        obsidian=False,
        no_memory=False,
        no_rag=False,
        rebuild=False,
    )
    rc = _invoke_audit(args)
    assert rc == 0

    # Memory files should NOT be touched.
    assert abs(lessons.stat().st_mtime - past) < 1.0
    assert abs(corpus.stat().st_mtime - past) < 1.0

    # And a one-line .skip_log.jsonl entry should have been appended.
    skip_log = lessons.parent / ".skip_log.jsonl"
    assert skip_log.exists()
    lines = [json.loads(line) for line in skip_log.read_text().splitlines() if line.strip()]
    assert lines
    last = lines[-1]
    assert last.get("run_dir", "").endswith("case-skip")
    assert "reason" in last


def test_is_skip_memory_run_honors_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agentic_swmm.memory.audit_hook import is_skip_memory_run

    run_dir = _make_run_dir(tmp_path)
    monkeypatch.setenv("AISWMM_SKIP_MEMORY", "1")
    skip, reason = is_skip_memory_run(run_dir)
    assert skip is True
    assert "AISWMM_SKIP_MEMORY" in reason


def test_is_skip_memory_run_honors_provenance_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_swmm.memory.audit_hook import is_skip_memory_run

    monkeypatch.delenv("AISWMM_SKIP_MEMORY", raising=False)
    run_dir = _make_run_dir(tmp_path, category="acceptance")
    skip, reason = is_skip_memory_run(run_dir)
    assert skip is True
    assert "category" in reason.lower()


def test_is_skip_memory_run_matches_agent_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_swmm.memory.audit_hook import is_skip_memory_run

    monkeypatch.delenv("AISWMM_SKIP_MEMORY", raising=False)
    runs_dir = tmp_path / "runs" / "agent" / "agent-12345"
    audit_dir = runs_dir / "09_audit"
    audit_dir.mkdir(parents=True)
    (audit_dir / "experiment_provenance.json").write_text(
        json.dumps({"run_id": "agent-12345", "case_name": "X", "schema_version": "1.1"}),
        encoding="utf-8",
    )
    skip, reason = is_skip_memory_run(runs_dir)
    assert skip is True
    assert "agent" in reason.lower()
