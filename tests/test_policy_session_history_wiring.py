"""Wiring tests — ``decide_with_memory`` consults session-history first.

Round 6 adds the prompt-history-based half of PRD-07 Phase 3. When the
caller passes a ``trace_dir`` (and optional ``decision_point``) the
policy first checks ``agent_trace.jsonl`` for similar prior decisions.
A strong consensus there short-circuits the whole policy with
``auto_complete`` *before* the parametric_memory path runs, saving the
LLM disambiguation round-trip.

When the consultation is below the consensus threshold, the policy
falls back to the existing case-based path and behaviour matches the
~20 previously-pinned policy tests exactly.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord
from agentic_swmm.agent.memory_informed_policy import (
    PolicyDecision,
    decide_with_memory,
)


def _decision_event(
    utterance: str,
    field: str,
    value: object,
    *,
    timestamp: str = "2026-05-10T12:00:00Z",
    decision_point: str = "intent_disambiguate",
) -> dict[str, object]:
    return {
        "event": "memory_informed_decision",
        "decision_point": decision_point,
        "utterance": utterance,
        "field": field,
        "value_chosen": value,
        "confidence": "memory_informed",
        "timestamp_utc": timestamp,
    }


def _write_trace(trace_dir: Path, events: list[dict[str, object]]) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    with (trace_dir / "agent_trace.jsonl").open("a", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")


class LegacyCallersUnaffectedTests(unittest.TestCase):
    """No trace_dir → session-history consult never runs."""

    def test_no_trace_dir_falls_through_to_case_based(self) -> None:
        ctx = MemoryContext(
            parametric_hits=[
                ParametricRecord(
                    run_id="r1", case_name="saanich-b8"
                )
            ]
        )
        decision = decide_with_memory("run audit", ctx)
        self.assertEqual(decision.confidence, "auto_complete")
        self.assertEqual(decision.resolved_case, "saanich-b8")

    def test_no_trace_dir_empty_memory_returns_llm(self) -> None:
        decision = decide_with_memory("run audit", MemoryContext())
        self.assertEqual(decision.confidence, "llm")


class SessionHistoryShortCircuitsTests(unittest.TestCase):
    """Strong consensus in trace → auto_complete, parametric not queried."""

    def test_three_matching_decisions_auto_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event(
                        "plot tecnopolo", "case_name", "tecnopolo"
                    ),
                    _decision_event(
                        "plot tecnopolo run", "case_name", "tecnopolo"
                    ),
                    _decision_event(
                        "plot tecnopolo now", "case_name", "tecnopolo"
                    ),
                ],
            )
            # Note: utterance doesn't textually match any case in the
            # parametric_memory (which is empty here). Without the
            # session-history short-circuit we would land in ``llm``.
            decision = decide_with_memory(
                "plot tecnopolo",
                MemoryContext(),
                decision_point="intent_disambiguate",
                trace_dir=tracer,
            )

        self.assertEqual(decision.confidence, "auto_complete")
        self.assertEqual(decision.resolved_case, "tecnopolo")
        self.assertIn("session history", decision.reasoning.lower())

    def test_short_consensus_does_not_short_circuit(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event(
                        "plot saanich", "case_name", "OU2"
                    ),
                    _decision_event(
                        "plot saanich", "case_name", "OU3"
                    ),
                ],
            )
            decision = decide_with_memory(
                "plot saanich",
                MemoryContext(),
                decision_point="intent_disambiguate",
                trace_dir=tracer,
            )

        # 1-of-2 share = 0.5 < 0.66 default; fall through to ``llm``
        # because no other evidence is present.
        self.assertEqual(decision.confidence, "llm")


class TraceEventWrittenTests(unittest.TestCase):
    """Every session-history consultation appends a trace line."""

    def test_auto_complete_writes_session_history_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event(
                        "plot tecnopolo", "case_name", "tecnopolo"
                    ),
                    _decision_event(
                        "plot tecnopolo run", "case_name", "tecnopolo"
                    ),
                    _decision_event(
                        "plot tecnopolo now", "case_name", "tecnopolo"
                    ),
                ],
            )
            decide_with_memory(
                "plot tecnopolo",
                MemoryContext(),
                decision_point="intent_disambiguate",
                trace_dir=tracer,
            )

            lines = (
                (tracer / "agent_trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            )
        # The trace will contain the three seed rows + at least one
        # new row marking the session-history consultation.
        new_rows = [
            json.loads(l)
            for l in lines
            if "memory_consultation" in l or '"source": "session_history"' in l
        ]
        self.assertTrue(
            new_rows,
            "expected a session-history consult/decision row in trace",
        )

    def test_below_threshold_still_writes_consultation(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event(
                        "plot saanich", "case_name", "OU2"
                    ),
                    _decision_event(
                        "plot saanich", "case_name", "OU3"
                    ),
                ],
            )
            decide_with_memory(
                "plot saanich",
                MemoryContext(),
                decision_point="intent_disambiguate",
                trace_dir=tracer,
            )
            lines = (
                (tracer / "agent_trace.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            )
        events = [json.loads(l) for l in lines]
        kinds = [e.get("kind") for e in events if e.get("event") == "memory_consultation"]
        self.assertIn("session_history", kinds)


class SessionHistoryWinsOverParametricTests(unittest.TestCase):
    """Session-history consensus beats parametric (saves LLM call)."""

    def test_strong_session_history_skips_parametric_path(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event(
                        "plot tecnopolo", "case_name", "tecnopolo"
                    ),
                    _decision_event(
                        "plot tecnopolo run", "case_name", "tecnopolo"
                    ),
                    _decision_event(
                        "plot tecnopolo now", "case_name", "tecnopolo"
                    ),
                ],
            )
            # Parametric memory has a different case — would normally
            # auto_complete to "saanich-b8" via Rule 2.
            ctx = MemoryContext(
                parametric_hits=[
                    ParametricRecord(run_id="r1", case_name="saanich-b8")
                ]
            )
            decision = decide_with_memory(
                "plot tecnopolo",
                ctx,
                decision_point="intent_disambiguate",
                trace_dir=tracer,
            )

        self.assertEqual(decision.confidence, "auto_complete")
        # Session-history wins, not the parametric case.
        self.assertEqual(decision.resolved_case, "tecnopolo")


class DecisionFrozenTests(unittest.TestCase):
    """PolicyDecision returned from session-history path is frozen."""

    def test_auto_complete_decision_is_frozen(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event("plot todcreek", "case_name", "todcreek"),
                    _decision_event("plot todcreek", "case_name", "todcreek"),
                    _decision_event("plot todcreek", "case_name", "todcreek"),
                ],
            )
            decision = decide_with_memory(
                "plot todcreek",
                MemoryContext(),
                decision_point="intent_disambiguate",
                trace_dir=tracer,
            )
        with self.assertRaises(Exception):
            decision.confidence = "llm"  # type: ignore[misc]


class NonCaseNameFieldTests(unittest.TestCase):
    """Consensus on a non-case_name field also short-circuits the LLM."""

    def test_plot_node_consensus_returns_auto_complete_no_case(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(
                tracer,
                [
                    _decision_event(
                        "plot todcreek at outfall",
                        "plot_node",
                        "O1",
                        decision_point="plot_node_selection",
                    ),
                    _decision_event(
                        "plot todcreek outfall",
                        "plot_node",
                        "O1",
                        decision_point="plot_node_selection",
                    ),
                    _decision_event(
                        "plot todcreek outfall",
                        "plot_node",
                        "O1",
                        decision_point="plot_node_selection",
                    ),
                ],
            )
            decision = decide_with_memory(
                "plot todcreek outfall",
                MemoryContext(),
                decision_point="plot_node_selection",
                trace_dir=tracer,
            )

        self.assertEqual(decision.confidence, "auto_complete")
        # Non-case_name field → resolved_case stays None (the caller
        # consumes the consensus through PolicyDecision.candidates /
        # reasoning rather than resolved_case).
        self.assertIsNone(decision.resolved_case)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
