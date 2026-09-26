"""Tests for the M2 audit -> memory auto-trigger hook.

These tests exercise ``agentic_swmm/commands/audit.py:main`` against a
fixture run directory and assert that:

- the default invocation triggers the memory hook for the audited run,
- ``--no-memory`` tells the hook to skip,
- the skip-memory heuristic catches acceptance / agent / ci-tagged
  runs, writes a one-line .skip_log.jsonl entry and no ledger.

The audit subprocess that drives swmm-experiment-audit is patched
out so the tests run in pure Python and stay deterministic. (Until the
memory simplification of 2026-09-26 the hook also refreshed a lessons
file and a RAG corpus; those assertions went with them.)
"""

from __future__ import annotations

import argparse
import json
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


def _args(run_dir: Path, **overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = dict(
        run_dir=run_dir, compare_to=None, case_name=None, workflow_mode=None,
        objective=None, obsidian=False, no_memory=False, rebuild=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


def _record_hook_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import agentic_swmm.memory as memory_pkg

    calls: list[dict[str, Any]] = []

    def fake_refresh(run_dir: Path, **kwargs: Any) -> dict[str, Any]:
        calls.append({"run_dir": Path(run_dir), **kwargs})
        return {"skipped": bool(kwargs.get("no_memory")), "reason": "", "errors": []}

    monkeypatch.setattr(memory_pkg, "trigger_memory_refresh", fake_refresh)
    return calls


def test_default_audit_triggers_the_memory_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_audit_subprocess(monkeypatch)
    run_dir = _make_run_dir(tmp_path)
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))
    calls = _record_hook_calls(monkeypatch)

    assert _invoke_audit(_args(run_dir)) == 0
    assert calls == [{"run_dir": run_dir, "no_memory": False}]


def test_no_memory_flag_reaches_the_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_audit_subprocess(monkeypatch)
    run_dir = _make_run_dir(tmp_path)
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(run_dir.parent))
    calls = _record_hook_calls(monkeypatch)

    assert _invoke_audit(_args(run_dir, no_memory=True)) == 0
    assert calls == [{"run_dir": run_dir, "no_memory": True}]


def test_hook_no_longer_takes_a_rag_switch() -> None:
    import inspect

    from agentic_swmm.memory.audit_hook import trigger_memory_refresh

    assert "no_rag" not in inspect.signature(trigger_memory_refresh).parameters


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
    store = tmp_path / "memory" / "store"
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_dir))
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(store))

    assert _invoke_audit(_args(run_dir)) == 0

    # No ledger is written for a skipped run; only the skip log.
    assert sorted(path.name for path in store.iterdir()) == [".skip_log.jsonl"]
    lines = [json.loads(line) for line in (store / ".skip_log.jsonl").read_text().splitlines() if line.strip()]
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


def test_is_skip_memory_run_acceptance_under_nested_runs_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unrelated ancestor named ``runs`` must not defeat the
    ``runs/acceptance/`` skip: ``parts.index("runs")`` anchored the
    FIRST occurrence, so acceptance-run data under a nested
    ``.../runs/.../runs/acceptance/...`` path leaked into long-term
    memory (found 2026-08-08, reproduced)."""
    from agentic_swmm.memory.audit_hook import is_skip_memory_run

    monkeypatch.delenv("AISWMM_SKIP_MEMORY", raising=False)
    run_dir = tmp_path / "runs" / "archive" / "project" / "runs" / "acceptance" / "case-x"
    run_dir.mkdir(parents=True)
    skip, reason = is_skip_memory_run(run_dir)
    assert skip is True
    assert "acceptance" in reason


def test_is_skip_memory_run_plain_acceptance_path_still_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentic_swmm.memory.audit_hook import is_skip_memory_run

    monkeypatch.delenv("AISWMM_SKIP_MEMORY", raising=False)
    run_dir = tmp_path / "runs" / "acceptance" / "case-y"
    run_dir.mkdir(parents=True)
    skip, _reason = is_skip_memory_run(run_dir)
    assert skip is True


def test_is_skip_memory_run_ordinary_run_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``acceptance`` elsewhere in the path (not adjacent to a runs/
    component) must not trigger the skip."""
    from agentic_swmm.memory.audit_hook import is_skip_memory_run

    monkeypatch.delenv("AISWMM_SKIP_MEMORY", raising=False)
    run_dir = tmp_path / "acceptance" / "runs" / "real-case"
    run_dir.mkdir(parents=True)
    skip, _reason = is_skip_memory_run(run_dir)
    assert skip is False
