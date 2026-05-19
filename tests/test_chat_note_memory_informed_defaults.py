"""Chat-note "Memory-informed defaults" auto-render (PRD-07 §2).

The chat-note generator should emit a section describing the
defaults the agent inherited from memory, drawn from
``memory_informed_decision`` events on the agent trace. The section
must be omitted entirely when the trace carries no such events so a
brand-new project's chat note is not polluted with an empty table.
"""

from __future__ import annotations

import unittest

from agentic_swmm.audit.chat_note import (
    MemoryInformedDecision,
    build_chat_note,
    render_memory_informed_defaults_section,
)


class RenderMemoryInformedDefaultsSectionTests(unittest.TestCase):
    def test_empty_decisions_returns_empty_string(self) -> None:
        self.assertEqual(render_memory_informed_defaults_section([]), "")

    def test_single_decision_renders_three_column_table(self) -> None:
        section = render_memory_informed_defaults_section(
            [
                MemoryInformedDecision(
                    field="plot_node",
                    value="OU2",
                    source="5 of 5 prior Tecnopolo runs",
                )
            ]
        )
        self.assertIn("## Memory-informed defaults", section)
        self.assertIn("| Field | Value | Source |", section)
        self.assertIn("|---|---|---|", section)
        self.assertIn("| plot_node | OU2 | 5 of 5 prior Tecnopolo runs |", section)

    def test_multiple_decisions_preserve_order(self) -> None:
        section = render_memory_informed_defaults_section(
            [
                MemoryInformedDecision("plot_node", "OU2", "5 runs"),
                MemoryInformedDecision("rain_kind", "TIMESERIES", "3 runs"),
            ]
        )
        node_idx = section.index("plot_node")
        rain_idx = section.index("rain_kind")
        self.assertLess(node_idx, rain_idx)

    def test_pipe_in_value_is_escaped(self) -> None:
        section = render_memory_informed_defaults_section(
            [
                MemoryInformedDecision(
                    field="legend_label",
                    value="depth | invert",
                    source="memory",
                )
            ]
        )
        # The pipe must be escaped so the renderer does not silently
        # split the column.
        self.assertIn("depth \\| invert", section)


class BuildChatNoteSectionInclusionTests(unittest.TestCase):
    """The Memory-informed defaults section is auto-inserted iff there is data."""

    def _trace_with_memory_decisions(self) -> list[dict]:
        return [
            {"event": "user_prompt", "text": "audit OU2"},
            {
                "event": "memory_consultation",
                "kind": "workflow_defaults",
                "case_meta": {"case_name": "tecnopolo"},
                "queried_at_utc": "2026-05-19T00:00:00Z",
                "evidence_count": 5,
                "consensus_fields": ["plot_node"],
                "ambiguous_fields": [],
            },
            {
                "event": "memory_informed_decision",
                "field": "plot_node",
                "value_chosen": "OU2",
                "rationale": "5 of 5 prior Tecnopolo runs",
                "source_runs": ["run_a", "run_b", "run_c", "run_d", "run_e"],
            },
            {"event": "tool_call", "tool": "plot_run"},
            {"event": "tool_result", "ok": True, "summary": "plot saved"},
        ]

    def test_section_present_when_trace_has_memory_decision(self) -> None:
        note = build_chat_note(
            {"case_id": "tecnopolo", "goal": "audit OU2"},
            self._trace_with_memory_decisions(),
        )
        self.assertIn("## Memory-informed defaults", note)
        self.assertIn("| plot_node | OU2 | 5 of 5 prior Tecnopolo runs |", note)

    def test_section_omitted_when_no_memory_decisions(self) -> None:
        note = build_chat_note(
            {"case_id": "tecnopolo", "goal": "ad-hoc question"},
            [
                {"event": "user_prompt", "text": "what time is it"},
                {"event": "tool_call", "tool": "doctor"},
                {"event": "tool_result", "ok": True, "summary": "ok"},
            ],
        )
        self.assertNotIn("Memory-informed defaults", note)

    def test_decision_without_field_is_skipped(self) -> None:
        trace = [
            {"event": "memory_informed_decision", "value_chosen": "OU2"},
            {
                "event": "memory_informed_decision",
                "field": "plot_node",
                "value_chosen": "OU2",
                "rationale": "memory consensus",
            },
        ]
        note = build_chat_note({"case_id": "tecnopolo"}, trace)
        # Only the well-formed event renders a row; the bad one is
        # silently dropped.
        self.assertEqual(note.count("plot_node"), 1)

    def test_decision_without_rationale_falls_back_to_source_count(self) -> None:
        trace = [
            {
                "event": "memory_informed_decision",
                "field": "plot_node",
                "value_chosen": "OU2",
                "source_runs": ["a", "b", "c"],
            }
        ]
        note = build_chat_note({"case_id": "tecnopolo"}, trace)
        self.assertIn("plot_node", note)
        self.assertIn("3 prior run(s)", note)


if __name__ == "__main__":
    unittest.main()
