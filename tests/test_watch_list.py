"""The watch list and the stats (#565).

Skill refinement loop, ticket 6: the runs row carries the failed QA
checks and the diagnostics, rebuild fills them, the watch list and the
stats are computed on request and never written.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.memory import proposals as mod
from agentic_swmm.memory import store, watch
from agentic_swmm.memory.jsonl_store import append_row
from agentic_swmm.memory.run_failures import record_run_failures


def _fail(tool: str, summary: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


def _provenance(run_dir: Path, run_id: str, case: str, failed: list[str], errors: list[str], warnings: list[str], stamp: str = "2026-10-20T10:00:00Z") -> None:
    (run_dir / "09_audit").mkdir(parents=True, exist_ok=True)
    checks = [{"id": "runner_outputs_exist", "ok": True, "detail": ""}] + [{"id": name, "ok": False, "detail": "x"} for name in failed]
    diagnostics = [{"id": name, "severity": "error", "message": "m"} for name in errors] + [{"id": name, "severity": "warning", "message": "m"} for name in warnings]
    payload = {
        "schema_version": "1.1", "run_id": run_id, "case_name": case, "status": "fail" if failed else "pass",
        "qa": {"checks": checks, "status": "fail" if failed else "pass", "fail_count": len(failed), "pass_count": 1},
        "model_diagnostics": {"diagnostics": diagnostics, "error_count": len(errors), "status": "fail" if errors else "warning"},
        "metrics": {}, "generated_at_utc": stamp,
    }
    (run_dir / "09_audit" / "experiment_provenance.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ws"
    (root / "memory" / "store").mkdir(parents=True)
    (root / "memory" / "initial").mkdir()
    (root / "memory" / "initial" / "tool_notes.md").write_text("# Tool notes\n", encoding="utf-8")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(root / "memory" / "store"))
    monkeypatch.setenv("AISWMM_PROPOSALS_DIR", str(root / "memory" / "proposals"))
    monkeypatch.setenv("AISWMM_FACTS_DIR", str(root / "memory"))
    monkeypatch.setenv("AISWMM_TOOL_NOTES", str(root / "memory" / "initial" / "tool_notes.md"))
    monkeypatch.setattr(mod, "_checkout_root", lambda: root)
    return root


def test_the_runs_row_records_the_failed_checks_and_the_diagnostics(workspace: Path) -> None:
    run_dir = workspace / "runs" / "2026-10-20" / "100000_a_run"
    _provenance(run_dir, "100000_a_run", "a", ["builder_input_validation", "peak_metric_present"], ["continuity_error_high"], ["conduit_slope_suspicious"])
    row = store.record_run(workspace / "memory" / "store", run_dir)
    assert json.loads(row["qa_failed_checks"]) == ["builder_input_validation", "peak_metric_present"]
    assert json.loads(row["diagnostic_errors"]) == ["continuity_error_high"] and json.loads(row["diagnostic_warnings"]) == ["conduit_slope_suspicious"]
    db = store.db_path_for(workspace / "memory" / "store")
    with store.session_db.connect(db) as conn:
        stored = conn.execute("SELECT qa_failed_checks, diagnostic_errors FROM runs WHERE run_id = '100000_a_run'").fetchone()
    assert json.loads(stored[0]) == ["builder_input_validation", "peak_metric_present"]


def test_rebuild_fills_the_columns_for_older_runs(workspace: Path) -> None:
    for n, case in enumerate(("a", "b", "a")):
        _provenance(workspace / "runs" / "2026-10-20" / f"10000{n}_{case}_run", f"10000{n}_{case}_run", case, ["builder_input_validation"], [], [])
    result = store.rebuild(workspace / "memory" / "store", workspace / "runs")
    assert result["runs"] == 3
    data = watch.watch_list(workspace / "memory" / "store")
    (entry,) = data["qa_failed_checks"]
    assert entry["id"] == "builder_input_validation" and entry["runs"] == 3 and entry["cases"] == 2 and entry["cleared_gate"] is True


def test_the_watch_list_counts_runs_and_cases_and_marks_the_gate(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    store_dir = workspace / "memory" / "store"
    _provenance(workspace / "runs" / "d" / "100000_a_run", "100000_a_run", "a", ["builder_input_validation"], ["continuity_error_high"], ["conduit_slope_suspicious"], "2026-10-18T00:00:00Z")
    _provenance(workspace / "runs" / "d" / "110000_a_run", "110000_a_run", "a", ["builder_input_validation"], [], ["conduit_slope_suspicious"], "2026-10-19T00:00:00Z")
    _provenance(workspace / "runs" / "d" / "120000_b_run", "120000_b_run", "b", [], [], ["conduit_slope_suspicious"], "2026-10-20T00:00:00Z")
    for name in ("100000_a_run", "110000_a_run", "120000_b_run"):
        store.record_run(store_dir, workspace / "runs" / "d" / name)
    append_row(store_dir / "negative_lessons.jsonl", {"schema_version": "1.0", "run_id": "100000_a_run", "case_name": "a", "lesson_type": "continuity_fail", "parameters_tried": {}, "metric_observed": {}, "note": "", "recorded_at": "2026-10-18T00:00:00Z"})
    data = watch.watch_list(store_dir)
    assert data["qa_failed_checks"] == [{"id": "builder_input_validation", "runs": 2, "cases": 1, "last": "2026-10-19T00:00:00Z", "cleared_gate": False}]
    assert data["diagnostic_warnings"][0]["runs"] == 3 and data["diagnostic_warnings"][0]["cleared_gate"] is True
    assert data["diagnostic_errors"][0]["id"] == "continuity_error_high"
    assert data["negative_lessons"] == [{"id": "continuity_fail", "runs": 1, "cases": 1, "last": "2026-10-18T00:00:00Z", "cleared_gate": False}]
    assert data["promoted_still_failing"] == []
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=False, refresh=False, proposal_id=None, draft=None, no_draft=True, model=None, watch=True, stats=False)) == 0
    out = capsys.readouterr().out
    assert "QA checks that failed: 1" in out and "builder_input_validation" in out and "cleared the gate, no fix known" in out
    assert "Promoted but still failing: 0" in out


def _promoted_tool_proposal(workspace: Path, decided: str) -> mod.Proposal:
    store_dir = workspace / "memory" / "store"
    kelowna = "runs/live-test-2026-09/2026-09-05/103702_downtown-kelowna-bc_run"
    todcreek = "runs/live-test-2026-09/2026-09-26/044009_todcreek_run"
    for session, run_dir in (("100000_a_run", kelowna), ("110000_b_run", todcreek), ("120000_c_run", kelowna)):
        record_run_failures(store_dir / "run_failures.jsonl", session, [
            _fail("read_file", f"file not found: /w/{run_dir}/06_run/model.rpt", path=f"{run_dir}/06_run/model.rpt"),
            _ok("read_file", path=f"{run_dir}/06_runner/model.rpt"),
        ])
    (proposal,) = mod.propose_from_failures(store_dir)
    assert mod.promote(proposal.id)["ok"] is True
    reloaded = mod.load_all()[0]
    reloaded.decided_utc = decided
    mod._rewrite(reloaded)
    return mod.load_all()[0]


def test_a_promoted_pattern_that_keeps_failing_is_flagged(workspace: Path) -> None:
    store_dir = workspace / "memory" / "store"
    # the evidence rows were recorded now; the promotion is dated after them
    proposal = _promoted_tool_proposal(workspace, "2026-11-01T00:00:00Z")
    pattern = proposal.evidence["pattern"]
    assert watch.promoted_still_failing(store_dir) == []
    for n in range(3):
        append_row(store_dir / "run_failures.jsonl", {
            "schema_version": "1.2", "run_id": f"30000{n}_x_chat", "tool": "read_file", "failure_class": "path_resolution",
            "summary": "file not found: /w/runs/2026-10-21/300000_x_run/06_run/model.rpt", "recorded_at": f"2026-11-1{n}T00:00:00Z",
        })
    (flag,) = watch.promoted_still_failing(store_dir)
    assert flag["id"] == proposal.id and flag["runs_since"] == 3 and flag["pattern"] == pattern
    text = watch.render_watch_list(watch.watch_list(store_dir))
    assert "Promoted but still failing: 1" in text


def test_the_stats_count_months_recurrence_and_drafted_versus_template(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from agentic_swmm.commands import memory as memory_cmd

    store_dir = workspace / "memory" / "store"
    _promoted_tool_proposal(workspace, "2026-11-01T00:00:00Z")
    rejected = mod.propose_fact("imperial units", source_session_id="s2")
    mod.reject(rejected.id, reason="no")
    data = watch.proposal_stats(store_dir)
    months = data["months"]
    assert sum(m["proposed"] for m in months.values()) == 2
    assert months["2026-11"]["promoted"] == 1 and sum(m["rejected"] for m in months.values()) == 1
    assert data["promoted_template"] == 1 and data["promoted_drafted"] == 0
    assert data["recurrence_after_promotion"] == [{"id": "001", "target": "memory/initial/tool_notes.md", "runs_since_promotion": 0}]
    assert memory_cmd.proposals_main(argparse.Namespace(all=False, json=True, refresh=False, proposal_id=None, draft=None, no_draft=True, model=None, watch=False, stats=True)) == 0
    assert json.loads(capsys.readouterr().out)["promoted_template"] == 1
