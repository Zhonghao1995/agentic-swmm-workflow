"""Doctor lists the run-failure store and the campaign's knobs.

Live test 2026-09-03 (S32): after a campaign with several failing runs,
doctor still said of the empty negative_lessons store "lessons accumulate
as runs fail", while the store the failures actually fed
(run_failures.jsonl) was not listed at all, and the knobs added by the
campaign were absent from the "Runtime knobs" table.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_swmm.diagnostics.doctor_report import (
    collect_memory_store_status,
    collect_optout_status,
)


def _by_name(statuses):
    return {s.name: s for s in statuses}


def test_run_failures_store_is_listed_with_its_rows(tmp_path: Path) -> None:
    rf = tmp_path / "run_failures.jsonl"
    rf.write_text(
        "\n".join(json.dumps({"tool": "run_swmm_inp", "error": "INP not found"}) for _ in range(2)) + "\n",
        encoding="utf-8",
    )
    row = _by_name(collect_memory_store_status(tmp_path))["run_failures.jsonl"]
    assert row.exists is True
    assert row.row_count == 2
    assert row.severity == "OK"


def test_absent_run_failures_store_is_healthy_not_missing(tmp_path: Path) -> None:
    row = _by_name(collect_memory_store_status(tmp_path))["run_failures.jsonl"]
    assert row.exists is False
    assert row.severity == "OK"
    assert "first failed tool call" in (row.remediation or "")


def test_empty_negative_lessons_hint_names_the_store_that_takes_tool_failures(tmp_path: Path) -> None:
    (tmp_path / "negative_lessons.jsonl").write_text("", encoding="utf-8")
    row = _by_name(collect_memory_store_status(tmp_path))["negative_lessons.jsonl"]
    assert row.severity == "EMPTY"
    assert "modeling QA fails" in (row.remediation or "")
    assert "run_failures.jsonl" in (row.remediation or "")


def test_campaign_knobs_are_in_the_runtime_knobs_table() -> None:
    names = {flag.env_name for flag in collect_optout_status()}
    for knob in (
        "AISWMM_TOOL_SUBSET",
        "AISWMM_ALWAYS_INTROSPECT",
        "AISWMM_ALLOW_REPO_WRITES",
        "AISWMM_ALLOW_SOURCE_READS",
    ):
        assert knob in names, knob
