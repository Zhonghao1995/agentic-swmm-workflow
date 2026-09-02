"""The failure memory is read back at session start (finding F-09).

Live sessions S01 and S02 (2026-09-02): S01 recorded a refused
``run_allowed_command`` in run_failures.jsonl; S02, the next session, repeated
the identical refused command five times. The store had writers only. The
digest below is what the planner now sees at the top of every session.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agentic_swmm.agent.session_bootstrap import bootstrap_system_prompt
from agentic_swmm.memory.run_failures import (
    append_rows,
    recent_failure_digest,
    record_run_failures,
    resolve_store,
)

NOW = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


def _stamp(delta: timedelta) -> str:
    return (NOW - delta).isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("AISWMM_MEMORY_DIR", str(tmp_path))
    return resolve_store()


def _row(tool: str, summary: str, age: timedelta, failure_class: str = "tool_error") -> dict:
    return {
        "schema_version": "1.0",
        "run_id": "r",
        "tool": tool,
        "failure_class": failure_class,
        "summary": summary,
        "recorded_at": _stamp(age),
    }


class TestDigest:
    def test_empty_store_costs_nothing(self, store):
        assert recent_failure_digest(store, now=NOW) == ""
        assert "<recent-failures>" not in "\n".join(bootstrap_system_prompt(session_dir=Path("."), prior_session_state=None))

    def test_repeats_are_counted_and_lead(self, store):
        append_rows(store, [
            _row("run_allowed_command", "command is not allowlisted", timedelta(hours=2)),
            _row("run_allowed_command", "command is not allowlisted", timedelta(hours=1)),
            _row("run_allowed_command", "command is not allowlisted", timedelta(minutes=30)),
            _row("map_run", "map_run failed", timedelta(hours=3)),
        ])
        digest = recent_failure_digest(store, now=NOW)
        lines = digest.splitlines()
        assert lines[0] == "<recent-failures>" and lines[-1] == "</recent-failures>"
        assert lines[2] == "- run_allowed_command: command is not allowlisted (x3)"
        assert lines[3] == "- map_run: map_run failed"
        assert "read_rpt_summary" in lines[1]

    def test_old_rows_fall_out_of_the_window(self, store):
        append_rows(store, [
            _row("fetch_swmm_from_canada", "stage 'config_missing' failed", timedelta(days=30)),
            _row("audit_run", "audit_run failed", timedelta(days=2)),
        ])
        digest = recent_failure_digest(store, now=NOW, days=7)
        assert "config_missing" not in digest
        assert "audit_run failed" in digest

    def test_only_old_rows_means_no_block(self, store):
        append_rows(store, [_row("audit_run", "audit_run failed", timedelta(days=40))])
        assert recent_failure_digest(store, now=NOW) == ""

    def test_the_limit_keeps_the_block_short(self, store):
        append_rows(store, [_row(f"tool_{i}", f"failure {i}", timedelta(hours=i)) for i in range(20)])
        digest = recent_failure_digest(store, now=NOW, limit=5)
        assert digest.count("\n- ") == 5

    def test_unparseable_stamps_are_ignored(self, store):
        append_rows(store, [
            {**_row("audit_run", "audit_run failed", timedelta(hours=1)), "recorded_at": "not-a-date"},
            _row("plot_run", "plot_run failed", timedelta(hours=1)),
        ])
        digest = recent_failure_digest(store, now=NOW)
        assert "audit_run" not in digest and "plot_run" in digest


class TestSessionStart:
    def test_the_planner_sees_what_failed_last_time(self, store):
        # What S01 wrote, as the recorder writes it.
        record_run_failures(store, "104342_downtown-victoria-bc_run", [
            {"tool": "run_allowed_command", "ok": False, "summary": "command is not allowlisted"},
            {"tool": "read_file", "ok": True, "summary": "fine"},
        ])
        extras = bootstrap_system_prompt(session_dir=Path("."), prior_session_state=None)
        joined = "\n".join(extras)
        assert "<recent-failures>" in joined
        assert "run_allowed_command: command is not allowlisted" in joined

    def test_a_broken_store_never_blocks_the_turn(self, store):
        store.write_text("{not json\n", encoding="utf-8")
        extras = bootstrap_system_prompt(session_dir=Path("."), prior_session_state=None)
        assert isinstance(extras, list)
