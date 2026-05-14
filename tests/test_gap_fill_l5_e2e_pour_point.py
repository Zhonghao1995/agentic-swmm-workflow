"""End-to-end L5 pour-point scenario (PRD-GF-L5).

The scenario walks the full L5 path:

1. The LLM (mocked) calls ``request_gap_judgement`` with
   ``gap_kind="pour_point"``.
2. The handler invokes the enumerator (mocked LLM) which returns 3
   candidate cells with hydrological tradeoffs cited.
3. The per-gap UI (mocked) captures the user's pick + free-form note.
4. The recorder writes an L5 :class:`GapDecision` to
   ``gap_decisions.json`` with ``decided_by="human"`` and
   ``enumerator_llm_call_id`` cross-referencing the enumerator's
   ``llm_calls.jsonl`` entry.
5. The planner replan injection drops a ``user_clarification``
   message into the next LLM turn.

The full audit trail is asserted at the end: ledger contents +
cross-references + replan injection visible.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.gap_fill.protocol import GapCandidate
from agentic_swmm.gap_fill.recorder import read_gap_decisions
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _ScriptedPlannerProvider:
    def __init__(self, responses: list[ProviderToolResponse]) -> None:
        self._responses = list(responses)
        self.calls_received: list[list[dict[str, Any]]] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self.calls_received.append(list(input_items))
        if not self._responses:
            raise AssertionError("scripted planner provider exhausted")
        return self._responses.pop(0)


class _RealExecutor:
    """Drives the actual ToolRegistry (no canned results)."""

    def __init__(self, registry: AgentToolRegistry, session_dir: Path) -> None:
        self.registry = registry
        self.session_dir = session_dir
        self.results: list[dict[str, Any]] = []
        self.dry_run = False

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        result = self.registry.execute(call, self.session_dir)
        self.results.append(result)
        return result


def _enumerator_candidates() -> list[GapCandidate]:
    return [
        GapCandidate(
            id="cand_1",
            summary="Cell (123, 456)",
            tradeoff="highest flow accumulation; slope-direction noisy",
        ),
        GapCandidate(
            id="cand_2",
            summary="Cell (124, 456)",
            tradeoff="matches DEM ridge; lower flow accumulation",
        ),
        GapCandidate(
            id="cand_3",
            summary="Cell (123, 457)",
            tradeoff="noise candidate; useful as a control",
        ),
    ]


def _planner_tool_call() -> ProviderToolCall:
    return ProviderToolCall(
        call_id="c-pour-point-1",
        name="request_gap_judgement",
        arguments={
            "gap_kind": "pour_point",
            "context": {"workflow": "swmm-gis", "step": "qa"},
            "evidence_ref": "06_qa/pour_point_qa.json",
        },
    )


def _planner_responses() -> list[ProviderToolResponse]:
    return [
        ProviderToolResponse(
            text="",
            model="stub",
            response_id="r1",
            tool_calls=[_planner_tool_call()],
            raw={},
        ),
        ProviderToolResponse(
            text="Acknowledged the user's pick; will narrow the calibration window.",
            model="stub",
            response_id="final",
            tool_calls=[],
            raw={},
        ),
    ]


def _seed_audit_evidence(session_dir: Path) -> None:
    """Create the evidence_ref path the handler doesn't require but the
    scenario references.

    The handler does not validate ``evidence_ref`` against disk (it
    just records the string), so this is purely scene-dressing.
    """
    qa = session_dir / "06_qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "pour_point_qa.json").write_text(
        json.dumps({"flagged_cell": [123, 456], "candidates": 3}), encoding="utf-8"
    )


class L5PourPointE2ETests(unittest.TestCase):
    def test_full_l5_pour_point_path(self) -> None:
        provider = _ScriptedPlannerProvider(_planner_responses())
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            _seed_audit_evidence(session_dir)
            registry = AgentToolRegistry()
            executor = _RealExecutor(registry, session_dir)
            planner = OpenAIPlanner(
                provider=provider,  # type: ignore[arg-type]
                registry=registry,
                max_steps=4,
                verbose=False,
                emit=lambda text: None,
            )

            # Mock the enumerator + UI + the TTY probe so the handler
            # runs to completion without real I/O.
            with mock.patch(
                "agentic_swmm.gap_fill.llm_enumerator.enumerate_candidates",
                return_value=(_enumerator_candidates(), "enum-call-id-xyz"),
            ) as enum_mock, mock.patch(
                "agentic_swmm.gap_fill.ui_per_gap.prompt_judgement",
                return_value=("cand_2", "Picked the ridge match — flow accum noise."),
            ) as ui_mock, mock.patch(
                "agentic_swmm.agent.tool_registry._is_tty_for_l5",
                return_value=True,
            ):
                outcome = planner.run(
                    goal="tell me about this repository",
                    session_dir=session_dir,
                    trace_path=session_dir / "agent_trace.jsonl",
                    executor=executor,
                )

            # Step 1: tool was invoked with the L5 args.
            enum_mock.assert_called_once()
            ui_mock.assert_called_once()

            # Step 2: L5 record landed in gap_decisions.json with the
            # full set of L5 fields populated.
            decisions = read_gap_decisions(session_dir)
            self.assertEqual(len(decisions), 1)
            dec = decisions[0]
            self.assertEqual(dec.severity, "L5")
            self.assertEqual(dec.gap_kind, "pour_point")
            self.assertEqual(dec.user_pick, "cand_2")
            self.assertEqual(
                dec.user_note, "Picked the ridge match — flow accum noise."
            )
            self.assertEqual(dec.decided_by, "human")
            self.assertEqual(dec.resume_mode, "llm_replan")
            self.assertEqual(dec.enumerator_llm_call_id, "enum-call-id-xyz")
            self.assertEqual(len(dec.candidates), 3)

            # Step 3: ``human_decisions_ref`` points back into the
            # provenance ledger.
            self.assertIsNotNone(dec.human_decisions_ref)
            self.assertIn("experiment_provenance.json", dec.human_decisions_ref or "")
            prov = session_dir / "09_audit" / "experiment_provenance.json"
            self.assertTrue(prov.is_file())
            prov_payload = json.loads(prov.read_text(encoding="utf-8"))
            actions = [
                entry.get("action") for entry in prov_payload.get("human_decisions", [])
            ]
            self.assertIn("gap_fill_L5", actions)

            # Step 4: the enumerator call_id cross-references
            # llm_calls.jsonl. Our mocked enumerator does not actually
            # write to that file (the real one does), so we assert
            # the call_id is propagated end-to-end and reflected on
            # the decision; the real-world cross-reference is
            # exercised by ``tests/test_gap_fill_l5_enumerator.py``.
            self.assertEqual(dec.proposer.llm_call_id, "enum-call-id-xyz")

            # Step 5: planner saw the replan injection on its second
            # turn — there is a user_clarification message carrying
            # the gap_kind, user_pick, and user_note.
            self.assertGreaterEqual(len(provider.calls_received), 2)
            second_input = provider.calls_received[1]
            clarifications = [
                item
                for item in second_input
                if item.get("role") == "user"
                and "[gap_decision]" in (item.get("content") or "")
            ]
            self.assertEqual(len(clarifications), 1)
            content = clarifications[0]["content"]
            self.assertIn("gap_kind: pour_point", content)
            self.assertIn("cand_2", content)
            self.assertIn("ridge match", content)

            self.assertTrue(outcome.ok)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
