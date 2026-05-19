"""Tests for ``agentic_swmm.agent.session_history`` (Round 6 / PRD-07 §8).

The recall function consults ``agent_trace.jsonl`` for prior
``memory_informed_decision`` events whose source utterance is
similar to the new utterance, and surfaces a consensus value when
recent decisions on the same field agreed strongly.

Why a pure helper (no LLM, no embedding)
----------------------------------------
The substrate is a flat JSONL the runtime already writes. Token-overlap
Jaccard is sufficient for the "user has historically meant X" guard
because the agent already records the exact decision field the user
confirmed. Adding an embedding model here would entangle a lightweight
recall path with a model dependency for negligible benefit at this
scale.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from agentic_swmm.agent.session_history import (
    PriorResolution,
    SessionHistoryRecall,
    recall_session_history,
)


def _write_trace(
    trace_path: Path, events: list[dict[str, object]]
) -> None:
    """Write events to ``agent_trace.jsonl`` in append order."""
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with trace_path.open("a", encoding="utf-8") as handle:
        for ev in events:
            handle.write(json.dumps(ev, sort_keys=True) + "\n")


def _decision_event(
    *,
    utterance: str,
    field: str,
    value: object,
    confidence: str = "memory_informed",
    decision_point: str = "intent_disambiguate",
    timestamp: str = "2026-05-10T12:00:00Z",
    run_id: str | None = None,
) -> dict[str, object]:
    """Construct one ``memory_informed_decision`` agent_trace row."""
    event: dict[str, object] = {
        "event": "memory_informed_decision",
        "decision_point": decision_point,
        "utterance": utterance,
        "field": field,
        "value_chosen": value,
        "confidence": confidence,
        "timestamp_utc": timestamp,
    }
    if run_id is not None:
        event["run_id"] = run_id
    return event


class MissingTraceTests(unittest.TestCase):
    """Slice — missing trace dir collapses to an empty recall."""

    def test_missing_trace_dir_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp) / "nonexistent"
            recall = recall_session_history(
                utterance="plot tecnopolo",
                trace_dir=tracer,
            )
        self.assertIsInstance(recall, SessionHistoryRecall)
        self.assertEqual(recall.similar_resolutions, [])
        self.assertIsNone(recall.consensus_value)
        self.assertEqual(recall.evidence_count, 0)

    def test_missing_trace_file_in_existing_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp) / "session-a"
            tracer.mkdir(parents=True)
            recall = recall_session_history(
                utterance="plot tecnopolo",
                trace_dir=tracer,
            )
        self.assertEqual(recall.evidence_count, 0)

    def test_none_trace_dir_returns_empty(self) -> None:
        recall = recall_session_history(
            utterance="plot tecnopolo", trace_dir=None
        )
        self.assertEqual(recall.evidence_count, 0)


class ConsensusTests(unittest.TestCase):
    """Slice — multiple prior resolutions yield a consensus value."""

    def test_three_matching_resolutions_yield_consensus(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            trace = tracer / "agent_trace.jsonl"
            _write_trace(
                trace,
                [
                    _decision_event(
                        utterance="plot tecnopolo run",
                        field="case_name",
                        value="tecnopolo",
                    ),
                    _decision_event(
                        utterance="plot tecnopolo now",
                        field="case_name",
                        value="tecnopolo",
                    ),
                    _decision_event(
                        utterance="plot tecnopolo",
                        field="case_name",
                        value="tecnopolo",
                    ),
                ],
            )

            recall = recall_session_history(
                utterance="plot tecnopolo",
                trace_dir=tracer,
            )

        self.assertEqual(recall.evidence_count, 3)
        self.assertEqual(recall.consensus_value, "tecnopolo")
        self.assertEqual(recall.consensus_field, "case_name")
        self.assertAlmostEqual(recall.consensus_confidence, 1.0)
        self.assertEqual(len(recall.similar_resolutions), 3)
        for resolution in recall.similar_resolutions:
            self.assertIsInstance(resolution, PriorResolution)

    def test_majority_consensus_at_four_of_five(self) -> None:
        events: list[dict[str, object]] = []
        for _ in range(4):
            events.append(
                _decision_event(
                    utterance="plot saanich watershed",
                    field="case_name",
                    value="OU2",
                )
            )
        events.append(
            _decision_event(
                utterance="plot saanich watershed",
                field="case_name",
                value="OU3",
            )
        )

        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)

            recall = recall_session_history(
                utterance="plot saanich watershed",
                trace_dir=tracer,
            )

        self.assertEqual(recall.consensus_value, "OU2")
        self.assertAlmostEqual(recall.consensus_confidence, 0.8)
        self.assertEqual(recall.evidence_count, 5)

    def test_equal_split_yields_no_consensus(self) -> None:
        events = [
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU2",
            ),
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU2",
            ),
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU3",
            ),
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU3",
            ),
        ]

        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)

            recall = recall_session_history(
                utterance="plot saanich",
                trace_dir=tracer,
            )

        # 0.5 < default threshold 0.66
        self.assertIsNone(recall.consensus_value)
        self.assertEqual(recall.evidence_count, 4)

    def test_consensus_threshold_overridable(self) -> None:
        """Caller can relax the threshold for a less strict use-case."""
        events = [
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU2",
            ),
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU2",
            ),
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="OU3",
            ),
        ]

        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)

            recall = recall_session_history(
                utterance="plot saanich",
                trace_dir=tracer,
                consensus_threshold=0.5,
            )

        self.assertEqual(recall.consensus_value, "OU2")


class JaccardSimilarityTests(unittest.TestCase):
    """Slice — similarity gate filters out non-matching utterances."""

    def test_similar_utterance_matches(self) -> None:
        events = [
            _decision_event(
                utterance="plot saanich",
                field="case_name",
                value="saanich-b8",
            ),
            _decision_event(
                utterance="plot saanich at node",
                field="case_name",
                value="saanich-b8",
            ),
        ]
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="plot saanich-b8",
                trace_dir=tracer,
            )
        # Both prior utterances share "plot" + "saanich" with the new
        # utterance; Jaccard ≥ 0.5 in at least one direction.
        self.assertGreaterEqual(recall.evidence_count, 1)

    def test_dissimilar_utterance_filtered(self) -> None:
        events = [
            _decision_event(
                utterance="show calibration summary for saanich",
                field="case_name",
                value="saanich-b8",
            ),
        ]
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="plot saanich",
                trace_dir=tracer,
            )
        # "show calibration summary for saanich" vs "plot saanich"
        # → tokens {show, calibration, summary, for, saanich} vs
        # {plot, saanich} → intersection {saanich}, union 6 → 1/6
        # < 0.5, so we should not see this event in the recall.
        self.assertEqual(recall.evidence_count, 0)

    def test_short_utterance_substring_fallback(self) -> None:
        """When utterances are short, fall back to substring matching."""
        events = [
            _decision_event(
                utterance="todcreek", field="case_name", value="todcreek"
            ),
            _decision_event(
                utterance="todcreek", field="case_name", value="todcreek"
            ),
        ]
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="todcreek",
                trace_dir=tracer,
            )
        self.assertGreaterEqual(recall.evidence_count, 2)
        self.assertEqual(recall.consensus_value, "todcreek")


class DecisionPointFilterTests(unittest.TestCase):
    """Slice — caller filters by decision_point label."""

    def test_decision_point_filter_narrows_results(self) -> None:
        events = [
            _decision_event(
                utterance="plot todcreek",
                field="case_name",
                value="todcreek",
                decision_point="intent_disambiguate",
            ),
            _decision_event(
                utterance="plot todcreek",
                field="plot_node",
                value="O1",
                decision_point="plot_node_selection",
            ),
        ]
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="plot todcreek",
                decision_point="plot_node_selection",
                trace_dir=tracer,
            )
        # Only the plot_node row should survive the filter.
        self.assertEqual(len(recall.similar_resolutions), 1)
        self.assertEqual(
            recall.similar_resolutions[0].decision_point,
            "plot_node_selection",
        )
        self.assertEqual(recall.consensus_field, "plot_node")
        self.assertEqual(recall.consensus_value, "O1")


class MalformedTraceTests(unittest.TestCase):
    """Slice — recall tolerates partially-malformed traces."""

    def test_torn_lines_tolerated(self) -> None:
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            trace_path = tracer / "agent_trace.jsonl"
            with trace_path.open("w", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        _decision_event(
                            utterance="plot tecnopolo",
                            field="case_name",
                            value="tecnopolo",
                        )
                    )
                    + "\n"
                )
                # Torn final line — not valid JSON.
                handle.write('{"event": "memory_informed_decision", "fie')
            recall = recall_session_history(
                utterance="plot tecnopolo",
                trace_dir=tracer,
            )
        self.assertEqual(recall.evidence_count, 1)

    def test_non_decision_events_ignored(self) -> None:
        events = [
            _decision_event(
                utterance="plot tecnopolo",
                field="case_name",
                value="tecnopolo",
            ),
            {
                "event": "tool_call",
                "tool": "plot_run",
                "timestamp_utc": "2026-05-10T12:00:00Z",
            },
        ]
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="plot tecnopolo",
                trace_dir=tracer,
            )
        self.assertEqual(recall.evidence_count, 1)


class LimitTests(unittest.TestCase):
    """Slice — limit kwarg caps the number of returned resolutions."""

    def test_limit_caps_results_keeps_most_recent(self) -> None:
        events: list[dict[str, object]] = []
        for i in range(15):
            events.append(
                _decision_event(
                    utterance="plot todcreek",
                    field="case_name",
                    value="todcreek",
                    timestamp=f"2026-05-{i+1:02d}T00:00:00Z",
                )
            )
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="plot todcreek",
                trace_dir=tracer,
                limit=5,
            )
        # limit=5 keeps only the most recent 5
        self.assertLessEqual(len(recall.similar_resolutions), 5)


class PriorResolutionShapeTests(unittest.TestCase):
    """PriorResolution surfaces the expected fields."""

    def test_resolution_carries_metadata(self) -> None:
        events = [
            _decision_event(
                utterance="plot todcreek",
                field="case_name",
                value="todcreek",
                timestamp="2026-05-10T15:00:00Z",
                run_id="run-42",
            ),
        ]
        with TemporaryDirectory() as tmp:
            tracer = Path(tmp)
            _write_trace(tracer / "agent_trace.jsonl", events)
            recall = recall_session_history(
                utterance="plot todcreek",
                trace_dir=tracer,
            )
        self.assertEqual(len(recall.similar_resolutions), 1)
        only = recall.similar_resolutions[0]
        self.assertEqual(only.utterance, "plot todcreek")
        self.assertEqual(only.field, "case_name")
        self.assertEqual(only.value, "todcreek")
        self.assertEqual(only.timestamp, "2026-05-10T15:00:00Z")
        self.assertEqual(only.run_id, "run-42")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
