"""Tests for the memory-informed mirror events on agent_trace.jsonl.

PRD-07 §2 specifies two new event kinds — ``memory_consultation`` and
``memory_informed_decision`` — that mirror the per-run
``memory_trace.jsonl`` into the agent-level ``agent_trace.jsonl``.
The writers are thin helpers around the shared ``write_event``
function so they pick up the same timestamp stamp and JSON shape as
every other agent_trace event.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agentic_swmm.agent.reporting import (
    write_memory_consultation,
    write_memory_informed_decision,
)


def _read_lines(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class WriteMemoryConsultationTests(unittest.TestCase):
    def test_writes_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_memory_consultation(
                path,
                kind="workflow_defaults",
                case_meta={"case_name": "tecnopolo"},
                evidence_count=5,
                consensus_fields=["plot_node"],
                ambiguous_fields=["rain_kind"],
                queried_at_utc="2026-05-19T00:00:00Z",
            )
            rows = _read_lines(path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["event"], "memory_consultation")
        self.assertEqual(row["kind"], "workflow_defaults")
        self.assertEqual(row["case_meta"], {"case_name": "tecnopolo"})
        self.assertEqual(row["evidence_count"], 5)
        self.assertEqual(row["consensus_fields"], ["plot_node"])
        self.assertEqual(row["ambiguous_fields"], ["rain_kind"])
        self.assertEqual(row["queried_at_utc"], "2026-05-19T00:00:00Z")
        # write_event also stamps a wall-clock timestamp.
        self.assertIn("timestamp_utc", row)

    def test_optional_fields_default_safely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_memory_consultation(
                path,
                kind="planner_intent_disambiguation",
                case_meta=None,
                evidence_count=0,
            )
            rows = _read_lines(path)
        row = rows[0]
        self.assertEqual(row["case_meta"], {})
        self.assertEqual(row["consensus_fields"], [])
        self.assertEqual(row["ambiguous_fields"], [])
        self.assertNotIn("queried_at_utc", row)

    def test_jsonl_is_one_line_per_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_memory_consultation(
                path, kind="a", case_meta={}, evidence_count=0
            )
            write_memory_consultation(
                path, kind="b", case_meta={}, evidence_count=0
            )
            content = path.read_text(encoding="utf-8")
        self.assertEqual(len(content.strip().splitlines()), 2)


class WriteMemoryInformedDecisionTests(unittest.TestCase):
    def test_writes_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_memory_informed_decision(
                path,
                field="plot_node",
                value_chosen="OU2",
                rationale="5 of 5 prior Tecnopolo runs",
                source_runs=["run_a", "run_b"],
            )
            rows = _read_lines(path)
        row = rows[0]
        self.assertEqual(row["event"], "memory_informed_decision")
        self.assertEqual(row["field"], "plot_node")
        self.assertEqual(row["value_chosen"], "OU2")
        self.assertEqual(row["rationale"], "5 of 5 prior Tecnopolo runs")
        self.assertEqual(row["source_runs"], ["run_a", "run_b"])

    def test_source_runs_defaults_to_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_memory_informed_decision(
                path,
                field="rain_kind",
                value_chosen="TIMESERIES",
                rationale="memory consultation",
            )
            rows = _read_lines(path)
        self.assertEqual(rows[0]["source_runs"], [])

    def test_value_chosen_can_be_numeric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_memory_informed_decision(
                path,
                field="time_step_sec",
                value_chosen=60,
                rationale="memory consensus",
            )
            rows = _read_lines(path)
        self.assertEqual(rows[0]["value_chosen"], 60)


class EventsCoexistWithExistingTraceTests(unittest.TestCase):
    """A run that already has tool_call events must still parse cleanly."""

    def test_mixed_event_stream_parses(self) -> None:
        from agentic_swmm.agent.reporting import write_event

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent_trace.jsonl"
            write_event(path, {"event": "tool_call", "tool": "run_swmm_inp"})
            write_memory_consultation(
                path, kind="workflow_defaults", case_meta={}, evidence_count=3
            )
            write_memory_informed_decision(
                path,
                field="plot_node",
                value_chosen="OU2",
                rationale="memory",
            )
            write_event(path, {"event": "tool_result", "ok": True})
            rows = _read_lines(path)
        self.assertEqual(
            [row["event"] for row in rows],
            [
                "tool_call",
                "memory_consultation",
                "memory_informed_decision",
                "tool_result",
            ],
        )


if __name__ == "__main__":
    unittest.main()
