"""One database for project memory (memory simplification part 2, 2026-09-06).

The JSONL ledgers stay the append-only truth; ``memory.sqlite`` next to
them is the query surface, synced from the ledgers on read. ``runs`` is
filled by the audit hook. ``aiswmm memory rebuild`` rebuilds all of it
from the ledgers and ``runs/``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agentic_swmm.memory import store
from agentic_swmm.memory.jsonl_store import append_row


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _failure(run_id: str, tool: str, summary: str) -> dict:
    return {"schema_version": "1.0", "run_id": run_id, "tool": tool, "failure_class": "tool_error", "summary": summary, "recorded_at": _stamp()}


def test_a_missing_ledger_reads_empty_without_creating_the_database(tmp_path: Path) -> None:
    assert store.ledger_rows(tmp_path / "run_failures.jsonl") == []
    assert not (tmp_path / store.DB_FILENAME).exists()


def test_rows_come_back_through_the_table_and_new_appends_are_seen(tmp_path: Path) -> None:
    ledger = tmp_path / "run_failures.jsonl"
    append_row(ledger, _failure("r1", "read_file", "missing"))
    assert [r["run_id"] for r in store.ledger_rows(ledger)] == ["r1"]
    db = tmp_path / store.DB_FILENAME
    assert db.is_file()
    assert store.table_counts(db)["failures"] == 1
    # A later append (a new size / mtime) is picked up by the next read.
    append_row(ledger, _failure("r2", "read_file", "missing again"))
    assert [r["run_id"] for r in store.ledger_rows(ledger)] == ["r1", "r2"]
    assert store.table_counts(db)["failures"] == 2


def test_sync_is_idempotent_and_skips_an_unchanged_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "parametric_memory.jsonl"
    row = {"schema_version": "2.0", "run_id": "r1", "case_name": "tod", "recorded_utc": _stamp(), "qa_metrics": {"peak_flow_value": 1.5}}
    append_row(ledger, row)
    db = store.db_path_for(ledger)
    assert store.sync_ledger(db, ledger) == 1
    assert store.sync_ledger(db, ledger) == 0
    # Re-importing after the state row is forgotten adds nothing: the
    # fingerprint keeps the table free of duplicates.
    with store.session_db.connect(db) as conn:
        conn.execute("DELETE FROM ledger_state")
        conn.commit()
    assert store.sync_ledger(db, ledger) == 0
    assert store.table_counts(db)["parametric"] == 1


def test_the_readers_go_through_the_database(tmp_path: Path) -> None:
    from agentic_swmm.memory.calibration_memory import recall_calibration
    from agentic_swmm.memory.negative_lessons import recall_negative_lessons
    from agentic_swmm.memory.parametric_memory import recall_parametric
    from agentic_swmm.memory.run_failures import read_run_failures

    append_row(tmp_path / "parametric_memory.jsonl", {"schema_version": "2.0", "run_id": "p1", "case_name": "tod", "recorded_utc": _stamp(), "qa_metrics": {}})
    append_row(tmp_path / "calibration_memory.jsonl", {"schema_version": "1.0", "run_id": "c1", "case_name": "tod", "use_case": "stormwater_event", "algorithm": "sceua", "objective_name": "nse", "objective_value": 0.7, "parameters": {}, "created_at": _stamp()})
    append_row(tmp_path / "negative_lessons.jsonl", {"schema_version": "1.0", "run_id": "n1", "case_name": "tod", "lesson_type": "continuity_fail", "parameters_tried": {"n": 0.5}, "metric_observed": {"runoff": 12.0}, "note": "too rough", "recorded_at": _stamp()})
    append_row(tmp_path / "run_failures.jsonl", _failure("f1", "read_file", "missing"))
    assert [r["run_id"] for r in recall_parametric(tmp_path / "parametric_memory.jsonl", {"case_name": "tod"})] == ["p1"]
    assert [r["run_id"] for r in recall_calibration(tmp_path / "calibration_memory.jsonl", {"algorithm": "sceua"})] == ["c1"]
    assert [l.run_id for l in recall_negative_lessons(tmp_path / "negative_lessons.jsonl", {"case_name": "tod"})] == ["n1"]
    assert [f.run_id for f in read_run_failures(tmp_path / "run_failures.jsonl")] == ["f1"]
    counts = store.table_counts(tmp_path / store.DB_FILENAME)
    assert (counts["parametric"], counts["calibration"], counts["negative_lessons"], counts["failures"]) == (1, 1, 1, 1)


def test_the_memory_card_filters_by_case_through_the_table(tmp_path: Path) -> None:
    from agentic_swmm.memory.card import render_case_card

    append_row(tmp_path / "parametric_memory.jsonl", {"schema_version": "2.0", "run_id": "a", "case_name": "tod", "swmm_version": "5.2.4", "recorded_utc": _stamp(), "qa_metrics": {"peak_flow_value": 2.0}})
    append_row(tmp_path / "parametric_memory.jsonl", {"schema_version": "2.0", "run_id": "b", "case_name": "other", "swmm_version": "5.2.4", "recorded_utc": _stamp(), "qa_metrics": {"peak_flow_value": 9.0}})
    card = render_case_card(tmp_path, "tod")
    assert "runs recorded: 1" in card


def _write_run(runs_dir: Path, name: str, case: str, qa: str = "pass") -> Path:
    run_dir = runs_dir / "2026-09-06" / name
    (run_dir / "09_audit").mkdir(parents=True)
    (run_dir / "09_audit" / "experiment_provenance.json").write_text(json.dumps({
        "run_id": name, "case_name": case, "workflow_mode": "prepared_inp", "status": "pass",
        "qa": {"status": qa}, "model_diagnostics": {"status": "pass"},
        "metrics": {"swmm_return_code": 0, "peak_flow": {"value": 0.07}, "continuity_error": {"runoff": -0.02}},
        "generated_at_utc": "2026-09-06T00:00:00+00:00",
    }), encoding="utf-8")
    (run_dir / "memory_summary.json").write_text(json.dumps({"project_key": case, "failure_patterns": ["no_detected_failure"]}), encoding="utf-8")
    return run_dir


def test_the_audit_hook_records_a_runs_row(tmp_path: Path) -> None:
    from agentic_swmm.memory import audit_hook

    run_dir = _write_run(tmp_path / "runs", "120000_tod_run", "tod")
    store_dir = tmp_path / "memory" / "store"
    store_dir.mkdir(parents=True)
    ctx = audit_hook._RefreshContext(run_dir=run_dir, runs_dir=tmp_path / "runs", project_root=tmp_path, memory_dir=store_dir, lessons_path=store_dir / "lessons_learned.md", result={"errors": []})
    audit_hook._phase_runs_row(ctx)
    assert ctx.result["runs_row"] == "120000_tod_run"
    rows = store.runs_for_case(store.db_path_for(store_dir), "tod")
    assert len(rows) == 1
    assert rows[0]["qa_status"] == "pass" and rows[0]["project"] == "tod" and rows[0]["workflow_mode"] == "prepared_inp"
    assert json.loads(rows[0]["failure_patterns"]) == ["no_detected_failure"]


def test_rebuild_sets_the_old_database_aside_and_rebuilds_from_ledgers_and_runs(tmp_path: Path) -> None:
    store_dir = tmp_path / "memory" / "store"
    store_dir.mkdir(parents=True)
    append_row(store_dir / "run_failures.jsonl", _failure("f1", "read_file", "missing"))
    append_row(store_dir / "parametric_memory.jsonl", {"schema_version": "2.0", "run_id": "p1", "case_name": "tod", "recorded_utc": _stamp(), "qa_metrics": {}})
    runs_dir = tmp_path / "runs"
    _write_run(runs_dir, "120000_tod_run", "tod")
    _write_run(runs_dir, "130000_other_run", "other", qa="fail")
    db = store.db_path_for(store_dir)
    db.write_bytes(b"not a database")
    result = store.rebuild(store_dir, runs_dir)
    assert result["backup"] and Path(result["backup"]).read_bytes() == b"not a database"
    counts = store.table_counts(db)
    assert counts["failures"] == 1 and counts["parametric"] == 1 and counts["runs"] == 2
    assert result["runs"] == 2
    # Rebuilding again is stable.
    again = store.rebuild(store_dir, runs_dir)
    assert store.table_counts(db)["runs"] == 2 and again["ledgers"]["failures"] == 1


def test_the_cli_rebuild_verb_reports_counts(tmp_path: Path, monkeypatch, capsys) -> None:
    from agentic_swmm.cli import main as cli_main

    store_dir = tmp_path / "memory" / "store"
    store_dir.mkdir(parents=True)
    append_row(store_dir / "negative_lessons.jsonl", {"schema_version": "1.0", "run_id": "n1", "case_name": "tod", "lesson_type": "continuity_fail", "parameters_tried": {}, "metric_observed": {}, "note": "", "recorded_at": _stamp()})
    runs_dir = tmp_path / "runs"
    _write_run(runs_dir, "120000_tod_run", "tod")
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(store_dir))
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(runs_dir))
    rc = cli_main(["memory", "rebuild", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["negative_lessons"] == 1
    assert payload["counts"]["runs"] == 1
    assert Path(payload["db_path"]) == store_dir / store.DB_FILENAME
