"""The failure loop (memory simplification PR 3a, 2026-09-26).

Until now the run-failure ledger recorded what failed and the digest at
session start listed it; nothing recorded what fixed it, so the planner
rediscovered every fix. Now the call right after a failure, when it
worked, is the failure's ``fix``; a later failure with the same
``pattern`` gets that fix as a ``[failure_memory]`` item in the
planner's next turn; the trace says when a hint was shown and whether
the next successful call followed it.
"""
from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from typing import Any

from agentic_swmm.memory import session_db
from agentic_swmm.memory.run_failures import (
    describe_fix,
    failure_hint,
    failure_pattern,
    read_run_failures,
    recent_failure_digest,
    record_run_failures,
    resolve_store,
)
from agentic_swmm.memory.store import ledger_rows


def _fail(tool: str, summary: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": False, "summary": summary}


def _ok(tool: str, **args: Any) -> dict[str, Any]:
    return {"tool": tool, "args": args, "ok": True, "summary": "ok"}


# --- pattern -----------------------------------------------------------------


def test_pattern_collapses_paths_and_long_numbers_but_keeps_swmm_codes():
    a = failure_pattern("read_file", "path_resolution", "file not found: /Users/x/runs/2026-09-03/145956_a/model.rpt")
    b = failure_pattern("read_file", "path_resolution", "file not found: C:\\work\\runs\\2026-09-05\\091200_b\\model.rpt")
    assert a == b == "read_file:path_resolution:file not found: <path>"
    assert failure_pattern("run_swmm_inp", "swmm_error", "ERROR 303 at line 12") == "run_swmm_inp:swmm_error:error 303 at line 12"
    assert failure_pattern("t", "c", "size 123456 bytes") == "t:c:size # bytes"


# --- fix detection at session end -------------------------------------------


def test_retry_with_changed_argument_is_recorded_as_the_fix(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    results = [
        _fail("run_swmm_inp", "external INP file not found: examples/todcreek/model_nope.inp", inp_path="examples/todcreek/model_nope.inp"),
        _ok("run_swmm_inp", inp_path="examples/todcreek/model.inp"),
    ]
    (row,) = record_run_failures(store, "run-1", results)
    assert row.fix_tool == "run_swmm_inp"
    assert row.fix == "run_swmm_inp again with inp_path: examples/todcreek/model_nope.inp -> examples/todcreek/model.inp"
    (stored,) = read_run_failures(store)
    assert stored.fix == row.fix and stored.pattern == row.pattern
    raw = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert raw["schema_version"] == "1.1" and raw["pattern"] == row.pattern and raw["fix_tool"] == "run_swmm_inp"


def test_another_tool_right_after_the_failure_is_recorded_as_instead(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    results = [
        _fail("run_allowed_command", "command is not allowlisted", command="python -c 'print(1)'"),
        _ok("read_rpt_summary", rpt_path="runs/x/06_run/model.rpt"),
    ]
    (row,) = record_run_failures(store, "run-2", results)
    assert row.fix == "read_rpt_summary(rpt_path=runs/x/06_run/model.rpt) instead"
    assert row.fix_tool == "read_rpt_summary"


def test_a_later_success_of_the_same_tool_beats_the_next_call(tmp_path: Path):
    """F-171 (S71d): the refused wc -l was fixed by run_allowed_command running pytest
    twelve steps later, not by the list_dir that happened to come next."""
    store = tmp_path / "run_failures.jsonl"
    results = [
        _fail("run_allowed_command", "command is not allowlisted", command=["wc", "-l", "a.inp"]),
        _ok("list_dir", path="scripts"),
        _ok("search_files", query="x"),
        _ok("run_allowed_command", command=["python", "-m", "pytest", "runs/s/_agent/test_count.py"]),
    ]
    (row,) = record_run_failures(store, "run-4", results)
    assert row.fix_tool == "run_allowed_command"
    assert row.fix == 'run_allowed_command again with command: ["wc", "-l", "a.inp"] -> ["python", "-m", "pytest", "runs/s/_agent/test_count.py"]'


def test_the_plan_supplies_the_arguments_as_called(tmp_path: Path):
    """F-170 (S71d): apply_patch reports path_count instead of the patch on success,
    so the fix read "patch dropped; path_count=1 added" until the plan's arguments won."""
    from agentic_swmm.agent.types import ToolCall

    store = tmp_path / "run_failures.jsonl"
    results = [
        {"tool": "apply_patch", "args": {"patch": "*** Begin Patch\n+++ scripts/x.mjs", "allow_evidence_edits": False}, "ok": False, "summary": "patch writes code into the product tree: scripts/x.mjs"},
        {"tool": "apply_patch", "args": {"path_count": 1, "allow_evidence_edits": False}, "ok": True, "summary": "applied envelope patch: 1 file op(s)"},
    ]
    calls = [
        ToolCall("apply_patch", {"patch": "*** Begin Patch\n+++ scripts/x.mjs", "allow_evidence_edits": False}),
        ToolCall("apply_patch", {"patch": "*** Begin Patch\n+++ runs/s/_agent/x.mjs", "allow_evidence_edits": False}),
    ]
    (row,) = record_run_failures(store, "run-5", results, calls=calls)
    assert row.fix == "apply_patch again with patch: *** Begin Patch\n+++ scripts/x.mjs -> *** Begin Patch\n+++ runs/s/_agent/x.mjs"
    # without the plan, or with a plan that does not align, the result args are used as before
    (row,) = record_run_failures(tmp_path / "other.jsonl", "run-6", results, calls=calls[:1])
    assert "path_count=1 added" in row.fix


def test_no_fix_when_the_next_call_also_failed_or_was_skipped(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    results = [
        _fail("read_file", "file not found: a.json", path="a.json"),
        _fail("read_file", "file not found: b.json", path="b.json"),
        {"tool": "list_dir", "args": {}, "ok": False, "skipped": True, "summary": "skipped: an earlier tool in this batch failed"},
    ]
    rows = record_run_failures(store, "run-3", results)
    assert [row.fix for row in rows] == ["", "", ""]
    raw = [json.loads(line) for line in store.read_text(encoding="utf-8").splitlines()]
    assert all("fix" not in row for row in raw)


def test_plain_retry_that_worked_is_a_fix_too():
    failed = _fail("plot_run", "MCP transport failed: process ended", run_dir="runs/x")
    _, fix = describe_fix(failed, _ok("plot_run", run_dir="runs/x"))
    assert fix == "plot_run again with the same arguments"


def test_fix_values_are_shortened():
    failed = _fail("apply_patch", "patch did not apply", patch="x" * 500)
    _, fix = describe_fix(failed, _ok("apply_patch", patch="y" * 500))
    assert len(fix) <= 300 and "..." in fix


# --- the hint at failure time -----------------------------------------------


def test_hint_names_the_last_fix_for_the_same_pattern(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    record_run_failures(store, "run-1", [
        _fail("read_file", "file not found: /a/runs/2026-09-03/145956_a/model_diagnostics.json", path="x"),
        _ok("read_file", path="runs/2026-09-03/145956_a/09_audit/model_diagnostics.json"),
    ])
    record_run_failures(store, "run-2", [
        _fail("read_file", "file not found: /a/runs/2026-09-04/101010_b/model_diagnostics.json", path="y"),
    ])
    later = _fail("read_file", "file not found: /a/runs/2026-09-05/111111_c/model_diagnostics.json", path="z")
    hint = failure_hint(later, store)
    assert hint is not None
    assert hint["fix_tool"] == "read_file"
    assert "seen: 2 time(s) in this project, recovered 1 time(s)" in hint["content"]
    assert "last fix: read_file again with path: x -> runs/2026-09-03/145956_a/09_audit/model_diagnostics.json" in hint["content"]
    assert hint["content"].startswith("[failure_memory]\ntool: read_file\n")


def test_no_hint_without_a_recorded_fix_or_for_a_denial(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    record_run_failures(store, "run-1", [_fail("map_run", "map_run failed", run_dir="r")])
    assert failure_hint(_fail("map_run", "map_run failed", run_dir="r"), store) is None
    assert failure_hint({"tool": "run_swmm_inp", "ok": False, "summary": "tool not approved by user"}, store) is None
    assert failure_hint(_ok("map_run"), store) is None


def test_rows_written_before_1_1_still_count_as_seen(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    old = {"schema_version": "1.0", "run_id": "old", "tool": "map_run", "failure_class": "tool_error",
           "summary": "map_run failed", "recorded_at": "2026-09-01T00:00:00Z"}
    store.write_text(json.dumps(old) + "\n", encoding="utf-8")
    record_run_failures(store, "run-1", [_fail("map_run", "map_run failed", run_dir="r"), _ok("map_run", run_dir="runs/r")])
    hint = failure_hint(_fail("map_run", "map_run failed", run_dir="q"), store)
    assert hint is not None and "seen: 2 time(s)" in hint["content"]


# --- the digest at session start --------------------------------------------


def test_digest_line_carries_the_known_fix(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    record_run_failures(store, "run-1", [
        _fail("run_allowed_command", "command is not allowlisted", command="python -c 1"),
        _ok("read_rpt_summary", rpt_path="r.rpt"),
    ])
    record_run_failures(store, "run-2", [_fail("map_run", "map_run failed", run_dir="r")])
    lines = recent_failure_digest(store).splitlines()
    assert lines[2] == "- run_allowed_command: command is not allowlisted -> fixed last time by: read_rpt_summary(rpt_path=r.rpt) instead"
    assert lines[3] == "- map_run: map_run failed"
    assert "fix recorded next to a line" in lines[1]


# --- the database -----------------------------------------------------------


def test_failures_table_carries_pattern_and_fix_columns(tmp_path: Path):
    store = tmp_path / "run_failures.jsonl"
    record_run_failures(store, "run-1", [_fail("map_run", "map_run failed", run_dir="r"), _ok("map_run", run_dir="runs/r")])
    ledger_rows(store)  # sync
    with sqlite3.connect(tmp_path / "memory.sqlite") as conn:
        (pattern, fix) = conn.execute("SELECT pattern, fix FROM failures").fetchone()
    assert pattern == "map_run:tool_error:map_run failed"
    assert fix == "map_run again with run_dir: r -> runs/r"


def test_database_created_before_the_columns_gets_them_in_place(tmp_path: Path):
    db_path = tmp_path / "memory.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE failures (id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT UNIQUE, run_id TEXT, tool TEXT, failure_class TEXT, summary TEXT, recorded_at TEXT, raw TEXT)")
        conn.execute("INSERT INTO failures (fingerprint, tool, raw) VALUES ('f', 'map_run', '{\"tool\": \"map_run\"}')")
    session_db.initialize(db_path)
    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(failures)")]
        kept = conn.execute("SELECT tool FROM failures").fetchone()[0]
    assert "pattern" in columns and "fix" in columns and kept == "map_run"
    store = tmp_path / "run_failures.jsonl"
    record_run_failures(store, "run-1", [_fail("map_run", "map_run failed", run_dir="r"), _ok("map_run", run_dir="runs/r")])
    assert len(ledger_rows(store)) == 2  # the old row and the synced one


# --- the planner: the same failure twice ------------------------------------

from agentic_swmm.agent.planner import Planner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderToolResponse]) -> None:
        self._responses = list(responses)
        self.calls_received: list[list[dict[str, Any]]] = []

    def respond_with_tools(self, *, system_prompt, input_items, tools, previous_response_id=None):
        self.calls_received.append(list(input_items))
        return self._responses.pop(0)


class _ScriptedExecutor:
    def __init__(self, tool_results: dict[str, list[dict[str, Any]]]) -> None:
        self._tool_results = tool_results
        self.results: list[dict[str, Any]] = []
        self.dry_run = False

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        queue = self._tool_results.get(call.name)
        result = dict(queue.pop(0)) if queue else {"ok": True, "summary": "ok"}
        result.setdefault("tool", call.name)
        result.setdefault("args", call.args)
        self.results.append(result)
        return result


def _turn(calls: list[ProviderToolCall], response_id: str) -> ProviderToolResponse:
    return ProviderToolResponse(text="", model="stub", response_id=response_id, tool_calls=calls, raw={})


def _events(trace_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_second_session_gets_the_first_sessions_fix_at_failure_time(tmp_path: Path):
    """The spec's criterion: the same failure twice, the second planning turn carries the fix."""
    summary = "file not found: /repo/runs/2026-09-03/145956_a/model_diagnostics.json"
    # Session 1 recorded the failure and the retry that worked (the runtime does this at session end).
    record_run_failures(resolve_store(), "session-1", [
        _fail("read_file", summary, path="model_diagnostics.json"),
        _ok("read_file", path="09_audit/model_diagnostics.json"),
    ])
    # Session 2: the same failure, then the planner follows the hint.
    provider = _ScriptedProvider([
        _turn([ProviderToolCall(call_id="c1", name="read_file", arguments={"path": "model_diagnostics.json"})], "r1"),
        _turn([ProviderToolCall(call_id="c2", name="read_file", arguments={"path": "09_audit/model_diagnostics.json"})], "r2"),
        ProviderToolResponse(text="done", model="stub", response_id="r3", tool_calls=[], raw={}),
    ])
    executor = _ScriptedExecutor({"read_file": [{"ok": False, "summary": summary.replace("145956_a", "101010_b")}]})
    planner = Planner(provider=provider, registry=AgentToolRegistry(), max_steps=6, verbose=False,
                      emit=lambda text: None, progress_stream=io.StringIO())
    session_dir = tmp_path / "session-2"
    session_dir.mkdir()
    trace_path = session_dir / "agent_trace.jsonl"
    run = planner.run(goal="tell me about this repository", executor=executor, session_dir=session_dir, trace_path=trace_path)
    assert run.ok

    second_input = provider.calls_received[1]
    hints = [item for item in second_input if item.get("role") == "user" and "[failure_memory]" in (item.get("content") or "")]
    assert len(hints) == 1
    content = hints[0]["content"]
    assert "last fix: read_file again with path: model_diagnostics.json -> 09_audit/model_diagnostics.json" in content
    assert "seen: 1 time(s) in this project, recovered 1 time(s)" in content
    # The function_call_output for the failed call comes before the hint.
    kinds = [item.get("type") or item.get("role") for item in second_input]
    assert kinds == ["function_call_output", "user"]

    events = {event["event"]: event for event in _events(trace_path) if event.get("event", "").startswith("failure_hint")}
    assert events["failure_hint_shown"]["tool"] == "read_file"
    assert events["failure_hint_shown"]["fix"].startswith("read_file again with path:")
    assert events["failure_hint_followed"]["followed"] is True


def test_no_hint_item_when_nothing_fixed_this_failure_before(tmp_path: Path):
    provider = _ScriptedProvider([
        _turn([ProviderToolCall(call_id="c1", name="read_file", arguments={"path": "a"})], "r1"),
        ProviderToolResponse(text="could not read it", model="stub", response_id="r2", tool_calls=[], raw={}),
    ])
    executor = _ScriptedExecutor({"read_file": [{"ok": False, "summary": "file not found: a"}]})
    planner = Planner(provider=provider, registry=AgentToolRegistry(), max_steps=4, verbose=False,
                      emit=lambda text: None, progress_stream=io.StringIO())
    session_dir = tmp_path / "s"
    session_dir.mkdir()
    trace_path = session_dir / "agent_trace.jsonl"
    planner.run(goal="tell me about this repository", executor=executor, session_dir=session_dir, trace_path=trace_path)
    assert all(item.get("role") != "user" for item in provider.calls_received[1])
    assert not [event for event in _events(trace_path) if event.get("event", "").startswith("failure_hint")]
