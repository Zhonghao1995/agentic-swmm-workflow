"""``agentic_swmm.memory.recall``: keyword recall over the store (PR 3b).

The pattern lookup in a generated ``lessons_learned.md`` is gone; recall
reads the failure and negative-lesson ledgers through their tables.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.memory.jsonl_store import append_row
from agentic_swmm.memory.recall import recall


def _seed(store: Path) -> None:
    append_row(store / "run_failures.jsonl", {
        "schema_version": "1.1", "run_id": "2026-09-03/145956_downtown-victoria-bc_run", "tool": "run_allowed_command",
        "failure_class": "tool_error", "summary": "command is not allowlisted", "recorded_at": "2026-09-03T14:59:56Z",
        "pattern": "run_allowed_command:tool_error:command is not allowlisted",
        "fix": "read_rpt_summary(rpt_path=runs/x/model.rpt) instead", "fix_tool": "read_rpt_summary",
    })
    append_row(store / "run_failures.jsonl", {
        "schema_version": "1.0", "run_id": "2026-09-04/101010_regina_run", "tool": "plot_run",
        "failure_class": "mcp_transport", "summary": "MCP transport failed: process ended", "recorded_at": "2026-09-04T10:10:10Z",
    })
    append_row(store / "negative_lessons.jsonl", {
        "schema_version": "1.0", "run_id": "r-neg", "case_name": "todcreek", "lesson_type": "continuity_fail",
        "parameters_tried": {"width": 900.0}, "metric_observed": {"runoff_continuity_pct": 12.5},
        "note": "postflight FAIL on runoff_continuity_pct", "recorded_at": "2026-09-01T00:00:00Z",
    })


def test_hits_carry_kind_text_and_the_fix(tmp_path: Path) -> None:
    _seed(tmp_path)
    hits = recall("command allowlisted", tmp_path)
    assert [hit["kind"] for hit in hits] == ["failure"]
    assert hits[0]["text"] == "run_allowed_command failed: command is not allowlisted; fixed by: read_rpt_summary(rpt_path=runs/x/model.rpt) instead"
    assert hits[0]["score"] == 2


def test_more_matching_tokens_rank_first_and_recent_breaks_ties(tmp_path: Path) -> None:
    _seed(tmp_path)
    hits = recall("failed transport continuity", tmp_path)
    kinds = [(hit["kind"], hit["score"]) for hit in hits]
    assert kinds[0] == ("failure", 2)  # plot_run: "failed" + "transport"
    assert {kind for kind, _ in kinds} == {"failure", "negative_lesson"}


def test_negative_lessons_filter_by_case_and_failures_are_project_wide(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert [hit["kind"] for hit in recall("continuity", tmp_path, case_name="todcreek")] == ["negative_lesson"]
    assert recall("continuity", tmp_path, case_name="other-case") == []
    boosted = recall("command", tmp_path, case_name="downtown-victoria-bc")
    assert boosted and boosted[0]["score"] == 2  # the run id carries the case


def test_chinese_query_matches_by_bigram(tmp_path: Path) -> None:
    append_row(tmp_path / "run_failures.jsonl", {
        "schema_version": "1.0", "run_id": "r", "tool": "fetch_swmm_from_canada", "failure_class": "tool_error",
        "summary": "取模超时 upstream timeout", "recorded_at": "2026-09-05T00:00:00Z",
    })
    assert recall("为什么取模会超时", tmp_path)[0]["kind"] == "failure"


def test_empty_query_or_empty_store_yield_nothing(tmp_path: Path) -> None:
    assert recall("", tmp_path) == []
    assert recall("anything", tmp_path) == []
    assert not (tmp_path / "memory.sqlite").exists()


def test_limit_caps_the_hits(tmp_path: Path) -> None:
    for index in range(8):
        append_row(tmp_path / "run_failures.jsonl", {
            "schema_version": "1.0", "run_id": f"r{index}", "tool": "read_file", "failure_class": "path_resolution",
            "summary": f"file not found: a{index}.json", "recorded_at": f"2026-09-0{index + 1}T00:00:00Z",
        })
    hits = recall("file not found", tmp_path, limit=3)
    assert len(hits) == 3 and hits[0]["run_id"] == "r7"
