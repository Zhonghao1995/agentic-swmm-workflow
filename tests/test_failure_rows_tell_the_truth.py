"""Failure rows tell the truth (skill refinement loop, ticket 1, #560).

A recorded fix names only the part of a value that changed, a pattern
never repeats the path token, refusals by policy are their own class
(recorded and hinted, never a lesson), a SWMM error that arrives through
the MCP transport is a SWMM error, and identical rows count as repeats.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentic_swmm.memory import store
from agentic_swmm.memory.jsonl_store import append_row
from agentic_swmm.memory.run_failures import (
    FAILURE_CLASSES,
    classify_failure,
    describe_fix,
    failure_hint,
    failure_pattern,
    read_run_failures,
    recent_failure_digest,
    record_run_failures,
)


def _fail(tool: str, summary: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


KELOWNA = "runs/live-test-2026-09/2026-09-05/103702_downtown-kelowna-bc_run"


# --- fix text ----------------------------------------------------------------


def test_fix_names_only_the_part_of_a_path_that_changed() -> None:
    failed = _fail("read_file", "file not found: x", path=f"{KELOWNA}/06_run/model.rpt")
    _, fix = describe_fix(failed, _ok("read_file", path=f"{KELOWNA}/06_runner/model.rpt"))
    assert fix == "read_file again with path: .../06_run/model.rpt -> .../06_runner/model.rpt"


def test_fix_keeps_the_tail_of_a_long_path() -> None:
    # F-174: values were cut at 80 characters and the useful tail was lost.
    long_dir = "runs/" + "x" * 120
    failed = _fail("read_rpt_summary", "file not found: x", rpt_path=f"{long_dir}/06_run/model.rpt")
    _, fix = describe_fix(failed, _ok("read_rpt_summary", rpt_path=f"{long_dir}/06_runner/model.rpt"))
    assert fix == "read_rpt_summary again with rpt_path: .../06_run/model.rpt -> .../06_runner/model.rpt"


def test_a_value_that_only_grew_shows_the_shared_part_once() -> None:
    failed = _fail("list_dir", "directory must exist", path="runs/a")
    _, fix = describe_fix(failed, _ok("list_dir", path="runs/a/06_runner"))
    assert fix == "list_dir again with path: .../a -> .../a/06_runner"


def test_fix_values_without_common_parts_are_still_capped() -> None:
    failed = _fail("apply_patch", "patch did not apply", patch="x" * 500)
    _, fix = describe_fix(failed, _ok("apply_patch", patch="y" * 500))
    assert len(fix) <= 300 and "..." in fix


# --- pattern -----------------------------------------------------------------


def test_pattern_never_repeats_the_path_token() -> None:
    pattern = failure_pattern(
        "read_file", "path_resolution", "file not found: /Users/z/Codex Project/Agentic SWMM/runs/a/model.rpt"
    )
    assert pattern == "read_file:path_resolution:file not found: <path>"


# --- classes -----------------------------------------------------------------


def test_refusals_by_policy_are_their_own_class() -> None:
    assert "policy_refusal" in FAILURE_CLASSES
    refusals = (
        "command is not allowlisted (allowed: pytest <repo test paths>)",
        "patch writes code into the product tree: scripts/count_lines.mjs",
        "patch modifies evidence/generated memory path; set allow_evidence_edits only for explicit regenerate tasks: runs/x",
    )
    for text in refusals:
        assert classify_failure(_fail("t", text)) == "policy_refusal", text


def test_a_policy_refusal_is_still_recorded_and_still_hinted(tmp_path: Path) -> None:
    ledger = tmp_path / "run_failures.jsonl"
    (row,) = record_run_failures(
        ledger,
        "run-1",
        [_fail("run_allowed_command", "command is not allowlisted (allowed: pytest)", command="wc -l a"), _ok("read_file", path="a")],
    )
    assert row.failure_class == "policy_refusal" and row.fix_tool == "read_file"
    hint = failure_hint(_fail("run_allowed_command", "command is not allowlisted (allowed: pytest)", command="wc -l b"), ledger)
    assert hint is not None and "read_file" in hint["fix"]


def test_a_swmm_error_through_the_mcp_transport_is_a_swmm_error() -> None:
    # F-176: the transport markers were tested first, so the class column lied.
    text = 'MCP transport failed: {"code": -32603, "message": "swmm_run failed: ERROR 209: undefined object O_MISSING at line 133 of [CONDUIT] section:\\n"}'
    assert classify_failure(_fail("run_swmm_inp", text)) == "swmm_error"
    assert classify_failure(_fail("run_swmm_inp", "MCP transport failed: MCP process ended before sending a complete line.")) == "mcp_transport"


def test_rows_recorded_with_the_old_class_read_back_with_the_true_one(tmp_path: Path) -> None:
    ledger = tmp_path / "run_failures.jsonl"
    text = 'MCP transport failed: {"code": -32603, "message": "swmm_run failed: ERROR 209: undefined object"}'
    append_row(
        ledger,
        {
            "schema_version": "1.1", "run_id": "r", "tool": "run_swmm_inp", "failure_class": "mcp_transport",
            "summary": text, "recorded_at": "2026-10-22T04:14:19Z", "pattern": "run_swmm_inp:mcp_transport:stale",
        },
    )
    (row,) = read_run_failures(ledger)
    assert row.failure_class == "swmm_error"
    assert row.pattern.startswith("run_swmm_inp:swmm_error:")


# --- repeats -----------------------------------------------------------------


def _refusal_row(run_id: str = "r") -> dict[str, Any]:
    return {
        "schema_version": "1.1", "run_id": run_id, "tool": "run_allowed_command", "failure_class": "policy_refusal",
        "summary": "command is not allowlisted", "recorded_at": "2026-09-02T18:00:56Z",
    }


def test_identical_rows_are_counted_as_repeats(tmp_path: Path) -> None:
    # F-173: seven byte-identical rows collapsed to one table row.
    ledger = tmp_path / "run_failures.jsonl"
    for _ in range(7):
        append_row(ledger, _refusal_row())
    assert len(read_run_failures(ledger)) == 7
    digest = recent_failure_digest(ledger, now=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert "(x7)" in digest


def test_a_database_with_the_old_fingerprints_is_reimported_without_doubling(tmp_path: Path) -> None:
    ledger = tmp_path / "run_failures.jsonl"
    append_row(ledger, _refusal_row("r1"))
    db = store.db_path_for(ledger)
    assert store.sync_ledger(db, ledger) == 1
    with store.session_db.connect(db) as conn:  # the row as the previous fingerprint wrote it: a bare hash
        conn.execute("UPDATE failures SET fingerprint = 'deadbeef'")
        conn.commit()
    append_row(ledger, _refusal_row("r2"))
    store.sync_ledger(db, ledger)
    assert store.table_counts(db)["failures"] == 2
    assert store.sync_ledger(db, ledger) == 0
