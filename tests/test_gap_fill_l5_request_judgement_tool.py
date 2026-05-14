"""Tests for the ``request_gap_judgement`` ToolSpec (PRD-GF-L5).

The ToolSpec is the agent's entry point for L5 subjective judgement.
The contract:

- Registered in :class:`AgentToolRegistry` under the name
  ``request_gap_judgement``.
- Schema requires ``gap_kind``, ``context``, ``evidence_ref``.
- ``is_read_only=False`` (judgement must never be QUICK-auto-approved).
- ``supports_gap_fill=False`` — L5 is *not* part of the L1/L3 gap-signal
  interception path; the LLM invokes this tool explicitly.
- Handler routes the call through the enumerator + per-gap UI +
  recorder, then returns ``{ok, decision_id, resume_mode: "llm_replan"}``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.gap_fill.protocol import GapCandidate


def _stub_candidates() -> list[GapCandidate]:
    return [
        GapCandidate(
            id="cand_1",
            summary="Cell (123, 456)",
            tradeoff="highest flow accumulation; slope-direction noisy.",
        ),
        GapCandidate(
            id="cand_2",
            summary="Cell (124, 456)",
            tradeoff="matches DEM ridge; lower flow accumulation.",
        ),
        GapCandidate(
            id="cand_3",
            summary="Cell (123, 457)",
            tradeoff="noise candidate; useful as a control.",
        ),
    ]


class RequestGapJudgementRegistrationTests(unittest.TestCase):
    """Schema + flags are wired correctly in the registry."""

    def test_tool_is_registered(self) -> None:
        registry = AgentToolRegistry()
        self.assertIn("request_gap_judgement", registry.names)

    def test_tool_is_not_read_only(self) -> None:
        registry = AgentToolRegistry()
        # QUICK profile must NEVER auto-approve L5 judgement.
        self.assertFalse(registry.is_read_only("request_gap_judgement"))

    def test_schema_required_args(self) -> None:
        registry = AgentToolRegistry()
        schemas = {schema["name"]: schema for schema in registry.schemas()}
        params = schemas["request_gap_judgement"]["parameters"]
        self.assertEqual(params["type"], "object")
        props = params["properties"]
        self.assertIn("gap_kind", props)
        self.assertIn("context", props)
        self.assertIn("evidence_ref", props)
        self.assertCountEqual(
            params["required"],
            ["gap_kind", "context", "evidence_ref"],
        )
        # gap_kind is an enum of the four documented L5 kinds.
        self.assertIn("enum", props["gap_kind"])
        self.assertIn("pour_point", props["gap_kind"]["enum"])
        self.assertIn("storm_event_selection", props["gap_kind"]["enum"])

    def test_tool_does_not_opt_in_to_gap_fill_state_machine(self) -> None:
        """L5 uses a different mechanism (LLM-invoked, not gap_signal)."""
        registry = AgentToolRegistry()
        # Reach into _tools to verify the supports_gap_fill flag — the
        # public surface (names / schemas) does not expose it.
        spec = registry._tools["request_gap_judgement"]
        self.assertFalse(spec.supports_gap_fill)


class RequestGapJudgementHandlerTests(unittest.TestCase):
    """Handler routes through enumerator → ui_per_gap → recorder."""

    def test_handler_records_l5_decision(self) -> None:
        from agentic_swmm.gap_fill.recorder import read_gap_decisions

        registry = AgentToolRegistry()
        call = ToolCall(
            "request_gap_judgement",
            {
                "gap_kind": "pour_point",
                "context": {"workflow": "swmm-gis", "step": "qa"},
                "evidence_ref": "06_qa/pour_point_qa.json",
            },
        )

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            # The enumerator and per-gap UI are stubbed: real LLM and
            # real input() would block the test.
            with mock.patch(
                "agentic_swmm.gap_fill.llm_enumerator.enumerate_candidates",
                return_value=(_stub_candidates(), "fake-call-id-001"),
            ), mock.patch(
                "agentic_swmm.gap_fill.ui_per_gap.prompt_judgement",
                return_value=("cand_2", "I picked the ridge match."),
            ):
                result = registry.execute(call, session_dir)

            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("resume_mode"), "llm_replan")
            decision_id = result.get("decision_id")
            self.assertTrue(decision_id, "handler must return a decision_id")

            decisions = read_gap_decisions(session_dir)
            self.assertEqual(len(decisions), 1)
            dec = decisions[0]
            self.assertEqual(dec.severity, "L5")
            self.assertEqual(dec.gap_kind, "pour_point")
            self.assertEqual(dec.user_pick, "cand_2")
            self.assertEqual(dec.user_note, "I picked the ridge match.")
            self.assertEqual(dec.enumerator_llm_call_id, "fake-call-id-001")
            self.assertEqual(dec.resume_mode, "llm_replan")
            self.assertEqual(dec.decided_by, "human")
            self.assertEqual(len(dec.candidates), 3)

    def test_handler_rejects_missing_args(self) -> None:
        registry = AgentToolRegistry()
        call = ToolCall("request_gap_judgement", {"gap_kind": "pour_point"})
        with TemporaryDirectory() as tmp:
            result = registry.execute(call, Path(tmp))
        self.assertFalse(result.get("ok"))
        self.assertIn("required", (result.get("summary") or "").lower())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
