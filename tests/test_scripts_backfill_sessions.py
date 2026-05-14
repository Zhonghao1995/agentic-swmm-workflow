"""``scripts/backfill_sessions.py`` end-to-end behaviour.

Drops three fixture session dirs under tmp_path, then exercises:
``--apply``, the idempotency contract, and ``--rebuild``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_backfill_module() -> object:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "backfill_sessions.py"
    spec = importlib.util.spec_from_file_location("_backfill_sessions", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_backfill_sessions"] = module
    spec.loader.exec_module(module)
    return module


def _write_session(runs_root: Path, leaf: str, case: str | None, kind: str = "run") -> Path:
    session_dir = runs_root / "2026-05-13" / leaf
    session_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {
            "event": "session_start",
            "goal": f"goal-{case or 'unknown'}",
            "timestamp_utc": "2026-05-13T22:00:00+00:00",
        },
        {
            "event": "planner_response",
            "step": 1,
            "text": f"text-{case or 'unknown'}",
            "tool_calls": [],
            "timestamp_utc": "2026-05-13T22:00:30+00:00",
        },
        {"event": "session_end", "ok": True, "timestamp_utc": "2026-05-13T22:01:00+00:00"},
    ]
    trace = session_dir / "agent_trace.jsonl"
    with trace.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")
    state = session_dir / "session_state.json"
    payload = {
        "goal": f"goal-{case or 'unknown'}",
        "planner": "openai",
        "model": "gpt-5.5",
        "ok": True,
        "workflow_state": {
            "active_run_dir": str(session_dir) if kind == "run" else None,
        },
    }
    state.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return session_dir


def test_backfill_dry_run_reports_counts(tmp_path: Path, capsys) -> None:
    backfill = _load_backfill_module()
    runs_root = tmp_path / "runs"
    _write_session(runs_root, "100000_tecnopolo_run", "tecnopolo", "run")
    _write_session(runs_root, "100100_hello_chat", "hello", "chat")
    db = tmp_path / "sessions.sqlite"
    rc = backfill.main(["--runs-root", str(runs_root), "--db-path", str(db)])
    assert rc == 0
    assert not db.exists()
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "discovered 2 session" in out


def test_backfill_apply_then_rerun_is_idempotent(tmp_path: Path) -> None:
    backfill = _load_backfill_module()
    runs_root = tmp_path / "runs"
    _write_session(runs_root, "100000_tecnopolo_run", "tecnopolo", "run")
    _write_session(runs_root, "100100_todcreek_run", "todcreek", "run")
    _write_session(runs_root, "100200_hello_chat", "hello", "chat")
    db = tmp_path / "sessions.sqlite"

    rc = backfill.main(["--runs-root", str(runs_root), "--db-path", str(db), "--apply"])
    assert rc == 0
    assert db.exists()

    from agentic_swmm.memory import session_db

    with session_db.connect(db) as conn:
        baseline_sessions = len(session_db.list_session_ids(conn))
        baseline_msgs = conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"]
    assert baseline_sessions == 3
    assert baseline_msgs >= 3

    # Re-apply must not duplicate.
    rc = backfill.main(["--runs-root", str(runs_root), "--db-path", str(db), "--apply"])
    assert rc == 0
    with session_db.connect(db) as conn:
        assert len(session_db.list_session_ids(conn)) == baseline_sessions
        assert conn.execute("SELECT COUNT(*) AS c FROM messages").fetchone()["c"] == baseline_msgs


def test_backfill_rebuild_wipes_and_refills(tmp_path: Path) -> None:
    backfill = _load_backfill_module()
    runs_root = tmp_path / "runs"
    _write_session(runs_root, "100000_tecnopolo_run", "tecnopolo", "run")
    db = tmp_path / "sessions.sqlite"

    backfill.main(["--runs-root", str(runs_root), "--db-path", str(db), "--apply"])
    assert db.exists()
    first_size = db.stat().st_size

    # Add another session, then rebuild.
    _write_session(runs_root, "100100_todcreek_run", "todcreek", "run")
    rc = backfill.main(
        ["--runs-root", str(runs_root), "--db-path", str(db), "--apply", "--rebuild"]
    )
    assert rc == 0

    from agentic_swmm.memory import session_db

    with session_db.connect(db) as conn:
        assert len(session_db.list_session_ids(conn)) == 2
    assert db.stat().st_size >= first_size
