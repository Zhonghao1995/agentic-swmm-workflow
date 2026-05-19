"""Tests for the HITL prompt formatter (PRD-06 Phase D.2).

The formatter is the user-facing surface when the memory-informed
policy escalates. These tests pin:

1. The output is a non-empty multi-line string for every input.
2. The escalation message appears verbatim in the prompt.
3. An empty MemoryContext still produces a usable prompt.
4. The closing question is always present.
5. The formatter never raises.
6. Memory stats (hit count, recency, metrics) appear when present.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.hitl_surface import format_hitl_prompt
from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord


def _make_hit(
    *,
    run_id: str,
    case_name: str,
    runoff_pct: float | None = None,
    flow_pct: float | None = None,
    recorded_utc: str | None = None,
) -> ParametricRecord:
    """Build a ParametricRecord with the QA fields the formatter reads."""
    qa: dict[str, float] = {}
    if runoff_pct is not None:
        qa["runoff_continuity_pct"] = runoff_pct
    if flow_pct is not None:
        qa["flow_continuity_pct"] = flow_pct
    return ParametricRecord(
        run_id=run_id,
        case_name=case_name,
        qa_metrics=qa,
        recorded_utc=recorded_utc,
    )


class FormatHitlPromptTests(unittest.TestCase):
    """Pin the formatter's contract."""

    def test_returns_non_empty_string_for_empty_context(self) -> None:
        prompt = format_hitl_prompt(
            "high-stakes action requested but memory has zero matching records",
            MemoryContext(),
        )
        self.assertIsInstance(prompt, str)
        self.assertGreater(len(prompt), 0)
        # The closing question must be present even on an empty context.
        self.assertIn("Please confirm or override.", prompt)

    def test_escalation_message_appears_verbatim(self) -> None:
        message = "high-stakes action requested but memory has zero matching records"
        prompt = format_hitl_prompt(message, MemoryContext())
        self.assertIn(message, prompt)

    def test_proposed_action_appears(self) -> None:
        prompt = format_hitl_prompt(
            "memory is empty",
            MemoryContext(),
            proposed_action="run accept-calibration on saanich-b8",
        )
        self.assertIn("run accept-calibration on saanich-b8", prompt)

    def test_missing_proposed_action_renders_placeholder(self) -> None:
        prompt = format_hitl_prompt("memory is empty", MemoryContext())
        self.assertIn("(no proposed action recorded)", prompt)

    def test_decision_point_appears_in_header(self) -> None:
        prompt = format_hitl_prompt(
            "memory is empty",
            MemoryContext(),
            decision_point="planner_intent_disambiguation",
        )
        self.assertIn("planner_intent_disambiguation", prompt)

    def test_memory_summary_appears(self) -> None:
        ctx = MemoryContext(
            parametric_hits=[],
            summary="3 prior runs of saanich-b8, mean runoff continuity 1.20%.",
        )
        prompt = format_hitl_prompt("test", ctx)
        self.assertIn("3 prior runs of saanich-b8", prompt)
        # The summary line is labelled so the user can find it quickly.
        self.assertIn("summary:", prompt)

    def test_hit_count_appears(self) -> None:
        hits = [
            _make_hit(run_id="r1", case_name="saanich-b8"),
            _make_hit(run_id="r2", case_name="saanich-b8"),
        ]
        ctx = MemoryContext(parametric_hits=hits)
        prompt = format_hitl_prompt("test", ctx)
        self.assertIn("parametric hits: 2", prompt)

    def test_zero_hits_shows_zero(self) -> None:
        ctx = MemoryContext(parametric_hits=[])
        prompt = format_hitl_prompt("test", ctx)
        self.assertIn("parametric hits: 0", prompt)

    def test_recency_line_appears_when_dated_hit_exists(self) -> None:
        hits = [
            _make_hit(
                run_id="r1",
                case_name="x",
                recorded_utc="2026-01-01T12:00:00Z",
            ),
            _make_hit(
                run_id="r2",
                case_name="x",
                recorded_utc="2026-05-15T09:30:00Z",
            ),
        ]
        ctx = MemoryContext(parametric_hits=hits)
        prompt = format_hitl_prompt("test", ctx)
        # The latest hit wins.
        self.assertIn("r2", prompt)
        self.assertIn("2026-05-15T09:30:00Z", prompt)

    def test_recency_line_omitted_when_no_dated_hits(self) -> None:
        # Empty recorded_utc on all hits means no recency line.
        hits = [_make_hit(run_id="r1", case_name="x")]
        ctx = MemoryContext(parametric_hits=hits)
        prompt = format_hitl_prompt("test", ctx)
        self.assertNotIn("most recent run:", prompt)

    def test_metric_stats_appear_when_numeric_data_exists(self) -> None:
        hits = [
            _make_hit(run_id="r1", case_name="x", runoff_pct=0.5),
            _make_hit(run_id="r2", case_name="x", runoff_pct=1.2),
            _make_hit(run_id="r3", case_name="x", runoff_pct=2.0),
        ]
        ctx = MemoryContext(parametric_hits=hits)
        prompt = format_hitl_prompt("test", ctx)
        self.assertIn("runoff continuity", prompt)
        self.assertIn("min=0.500", prompt)
        self.assertIn("max=2.000", prompt)

    def test_no_metric_lines_when_qa_metrics_empty(self) -> None:
        hits = [_make_hit(run_id="r1", case_name="x")]
        ctx = MemoryContext(parametric_hits=hits)
        prompt = format_hitl_prompt("test", ctx)
        # No "runoff continuity %" line should be present.
        self.assertNotIn("runoff continuity", prompt)

    def test_blank_escalation_renders_placeholder(self) -> None:
        prompt = format_hitl_prompt("", MemoryContext())
        self.assertIn("(no escalation message provided)", prompt)
        self.assertIn("Please confirm or override.", prompt)

    def test_never_raises_on_hostile_context(self) -> None:
        # A MemoryContext-shaped object whose summary attribute is
        # itself an exception-raising object must still produce a
        # prompt — the formatter has to be unconditionally safe.
        class _Hostile:
            parametric_hits: list = []

            @property
            def summary(self) -> str:
                raise RuntimeError("hostile summary")

        prompt = format_hitl_prompt("msg", _Hostile())  # type: ignore[arg-type]
        self.assertIn("Please confirm or override.", prompt)
        self.assertIsInstance(prompt, str)

    def test_unknown_decision_point_defaults_to_unknown(self) -> None:
        prompt = format_hitl_prompt(
            "test",
            MemoryContext(),
            decision_point="",
        )
        # Empty decision_point still produces a coherent header.
        self.assertIn("unknown", prompt)

    def test_prompt_is_multi_line(self) -> None:
        prompt = format_hitl_prompt("test", MemoryContext())
        lines = prompt.splitlines()
        self.assertGreaterEqual(
            len(lines),
            5,
            "HITL prompt should always be multi-line for readability",
        )


if __name__ == "__main__":
    unittest.main()
