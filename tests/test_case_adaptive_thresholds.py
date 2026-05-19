"""Tests for ``agentic_swmm.memory.case_adaptive_thresholds`` (PRD-07 Phase 4).

The proposal mechanism inspects ``calibration_memory.jsonl`` for a
given case + use_case and returns a tightened warn threshold (or the
default unchanged) plus a human-readable rationale. The proposal is
advisory — Phase 5 will wire it into a HITL gate.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.agent.memory_context import MemoryContext
from agentic_swmm.agent.memory_trace import (
    log_memory_decision,
    read_memory_trace,
)
from agentic_swmm.memory.case_adaptive_thresholds import (
    MIN_HISTORICAL_SAMPLES,
    propose_case_threshold,
)


def _write_calibration_jsonl(
    path: Path,
    *,
    case_name: str,
    use_case: str,
    metric: str,
    values: list[float],
    extra_filter: dict[str, Any] | None = None,
) -> None:
    """Write a JSONL of N calibration rows with the given metric values."""
    lines = []
    for i, v in enumerate(values):
        row: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": f"run-{i}",
            "case_name": case_name,
            "use_case": use_case,
            "secondary_metrics": {metric: v},
        }
        if extra_filter:
            row.update(extra_filter)
        lines.append(json.dumps(row, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class InsufficientHistoryTests(unittest.TestCase):
    def test_zero_records_returns_default(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        self.assertEqual(proposal["warn"], 5.0)
        self.assertEqual(proposal["fail"], 10.0)
        self.assertEqual(proposal["n_historical"], 0)
        self.assertIn("insufficient", proposal["rationale"])

    def test_fewer_than_minimum_returns_default(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            # MIN_HISTORICAL_SAMPLES - 1 rows is not enough.
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[0.1] * (MIN_HISTORICAL_SAMPLES - 1),
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        self.assertEqual(proposal["warn"], 5.0)
        self.assertEqual(proposal["n_historical"], MIN_HISTORICAL_SAMPLES - 1)


class TightenProposalTests(unittest.TestCase):
    def test_five_rows_under_half_pct_tightens_warn(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[0.1, 0.2, 0.3, 0.4, 0.5],
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        # Median = 0.3; proposal = max(0.6, 0.5) = 0.6 — tighter than 5.0.
        self.assertLess(proposal["warn"], 5.0)
        self.assertEqual(proposal["fail"], 10.0)
        self.assertEqual(proposal["n_historical"], 5)
        # Rationale must explain the tightening.
        self.assertIn("median", proposal["rationale"])
        self.assertTrue(proposal["rationale"])

    def test_proposed_warn_respects_floor(self) -> None:
        # Median is extremely tight; the proposal must not drop below
        # MIN_TIGHTEN_FACTOR * default_warn.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[0.001] * MIN_HISTORICAL_SAMPLES,
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        # 5.0 * 0.1 = 0.5 floor.
        self.assertGreaterEqual(proposal["warn"], 0.5)

    def test_history_at_or_above_default_does_not_loosen(self) -> None:
        # Median is well above the default warn; we never loosen.
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[6.0] * MIN_HISTORICAL_SAMPLES,
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        self.assertEqual(proposal["warn"], 5.0)
        self.assertIn("not tighter than default", proposal["rationale"])


class FilterScopingTests(unittest.TestCase):
    def test_only_matching_case_and_use_case_counted(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            # Write five matching rows + ten rows under a different case
            # / use_case combination. Only the matching ones must count.
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[0.2] * MIN_HISTORICAL_SAMPLES,
            )
            with store.open("a", encoding="utf-8") as h:
                for i in range(10):
                    h.write(
                        json.dumps(
                            {
                                "schema_version": "1.0",
                                "run_id": f"other-{i}",
                                "case_name": "different",
                                "use_case": "stormwater_event",
                                "secondary_metrics": {
                                    "runoff_continuity_pct": 50.0
                                },
                            },
                            sort_keys=True,
                        )
                        + "\n"
                    )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        self.assertEqual(proposal["n_historical"], MIN_HISTORICAL_SAMPLES)
        # Outlier rows must not pull median up.
        self.assertLess(proposal["warn"], 5.0)


class NullDefaultWarnTests(unittest.TestCase):
    def test_null_default_warn_returns_default_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[0.2] * MIN_HISTORICAL_SAMPLES,
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": None, "fail": None},
            )
        self.assertIsNone(proposal["warn"])
        self.assertIn("null", proposal["rationale"])


class MagnitudeHandlingTests(unittest.TestCase):
    def test_negative_continuity_values_use_magnitude(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "calibration_memory.jsonl"
            # SWMM continuity is signed; the proposal should treat
            # +0.4 and -0.4 identically.
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[-0.1, 0.1, -0.2, 0.2, -0.3],
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )
        # Median of |values| = 0.2 → proposal = max(0.4, 0.5) = 0.5
        self.assertAlmostEqual(proposal["warn"], 0.5)


class MemoryTraceIntegrationTests(unittest.TestCase):
    def test_proposal_can_be_traced_with_memory_informed_confidence(self) -> None:
        """The proposal mechanism is the input to a memory_trace line.

        Phase 4 only verifies that the proposal data is shaped for that
        trace; Phase 5 will wire it into the runtime gate.
        """
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            store = base / "calibration_memory.jsonl"
            _write_calibration_jsonl(
                store,
                case_name="saanich",
                use_case="stormwater_event",
                metric="runoff_continuity_pct",
                values=[0.1, 0.2, 0.3, 0.4, 0.5],
            )
            proposal = propose_case_threshold(
                "saanich",
                "stormwater_event",
                "runoff_continuity_pct",
                calibration_store=store,
                default_thresholds={"warn": 5.0, "fail": 10.0},
            )

            run_dir = base / "run"
            run_dir.mkdir()
            context = MemoryContext(summary=proposal["rationale"])
            log_memory_decision(
                run_dir=run_dir,
                decision_point="case_adaptive_threshold_proposal",
                context=context,
                decision=str(proposal["warn"]),
                confidence="memory_informed",
            )
            entries = read_memory_trace(run_dir)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["confidence"], "memory_informed")
        self.assertEqual(
            entries[0]["decision_point"], "case_adaptive_threshold_proposal"
        )
        self.assertTrue(entries[0]["memory_context_summary"])


if __name__ == "__main__":
    unittest.main()
