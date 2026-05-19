"""Tests for ``agentic_swmm.agent.memory_trace`` (PRD-07 Phase 2).

Transparency: every memory-consulting decision the agent makes should
leave an auditable JSONL line behind. The writer and reader are
intentionally trivial — the value is the contract (atomic per line,
torn-line robust) not the schema.

Slices covered here:

6. :func:`log_memory_decision` writes one schema-shaped JSONL line.
7. Confidence label whitelist (the 4 quadrants).
8. :func:`read_memory_trace` round-trip.
9. Torn final line is skipped, not raised.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord
from agentic_swmm.agent.memory_trace import (
    MEMORY_TRACE_FILENAME,
    VALID_CONFIDENCE_LABELS,
    log_memory_decision,
    read_memory_trace,
)


def _ctx_with(hits: int = 0, summary: str = "") -> MemoryContext:
    ctx = MemoryContext(summary=summary or f"{hits} prior runs of casex.")
    for i in range(hits):
        ctx.parametric_hits.append(
            ParametricRecord(run_id=f"run-{i}", case_name="casex")
        )
    ctx.reference_thresholds = {
        "runoff_continuity_pct": {"warn": 5.0, "fail": 10.0}
    }
    return ctx


class LogWritesOneLineTests(unittest.TestCase):
    """Slice 6 — one call writes exactly one parseable JSONL line."""

    def test_writes_one_line_with_expected_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            ctx = _ctx_with(hits=2, summary="2 prior runs of casex.")

            log_memory_decision(
                run_dir=run_dir,
                decision_point="audit_hook_parametric_write",
                context=ctx,
                decision="recorded",
                confidence="auto_complete",
            )

            trace = run_dir / MEMORY_TRACE_FILENAME
            self.assertTrue(trace.is_file())
            lines = trace.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])

        self.assertEqual(entry["decision_point"], "audit_hook_parametric_write")
        self.assertEqual(entry["confidence"], "auto_complete")
        self.assertEqual(entry["decision_taken"], "recorded")
        self.assertEqual(entry["parametric_hit_count"], 2)
        self.assertIn("runoff_continuity_pct", entry["thresholds_used"])
        self.assertIn("memory_context_summary", entry)
        self.assertIn("timestamp", entry)
        self.assertEqual(entry["schema_version"], "1.0")

    def test_multiple_calls_append_in_order(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            ctx = _ctx_with(hits=0)
            for label in ("first", "second", "third"):
                log_memory_decision(
                    run_dir=run_dir,
                    decision_point=label,
                    context=ctx,
                    decision="chose-default",
                    confidence="llm",
                )

            lines = (run_dir / MEMORY_TRACE_FILENAME).read_text(
                encoding="utf-8"
            ).splitlines()

        self.assertEqual(len(lines), 3)
        labels = [json.loads(line)["decision_point"] for line in lines]
        self.assertEqual(labels, ["first", "second", "third"])

    def test_creates_parent_directory(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "deep" / "nested" / "run-1"
            ctx = _ctx_with(hits=0)

            log_memory_decision(
                run_dir=run_dir,
                decision_point="dp",
                context=ctx,
                decision="x",
                confidence="memory_informed",
            )

            self.assertTrue((run_dir / MEMORY_TRACE_FILENAME).is_file())


class ConfidenceWhitelistTests(unittest.TestCase):
    """Slice 7 — confidence labels match the 4 quadrants from the PRD."""

    def test_valid_label_set(self) -> None:
        self.assertEqual(
            set(VALID_CONFIDENCE_LABELS),
            {"auto_complete", "memory_informed", "llm", "hitl"},
        )

    def test_invalid_confidence_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(ValueError) as cm:
                log_memory_decision(
                    run_dir=run_dir,
                    decision_point="dp",
                    context=_ctx_with(),
                    decision="x",
                    confidence="probably_fine",  # type: ignore[arg-type]
                )
            self.assertIn("confidence", str(cm.exception))

    def test_each_quadrant_round_trips(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for label in VALID_CONFIDENCE_LABELS:
                log_memory_decision(
                    run_dir=run_dir,
                    decision_point=f"dp_{label}",
                    context=_ctx_with(),
                    decision="x",
                    confidence=label,
                )

            entries = read_memory_trace(run_dir)
            seen = {e["confidence"] for e in entries}
        self.assertEqual(seen, set(VALID_CONFIDENCE_LABELS))


class ReadRoundTripTests(unittest.TestCase):
    """Slice 8 — read_memory_trace returns entries in write order."""

    def test_round_trip_preserves_decision_points(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            for i in range(4):
                log_memory_decision(
                    run_dir=run_dir,
                    decision_point=f"step_{i}",
                    context=_ctx_with(hits=i),
                    decision=f"value_{i}",
                    confidence="memory_informed",
                )
            entries = read_memory_trace(run_dir)
        self.assertEqual(
            [e["decision_point"] for e in entries],
            ["step_0", "step_1", "step_2", "step_3"],
        )
        self.assertEqual(
            [e["parametric_hit_count"] for e in entries],
            [0, 1, 2, 3],
        )

    def test_missing_trace_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            entries = read_memory_trace(Path(tmp))
        self.assertEqual(entries, [])


class TornFinalLineTests(unittest.TestCase):
    """Slice 9 — a partially-written final line must not crash readers."""

    def test_torn_line_is_skipped(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            log_memory_decision(
                run_dir=run_dir,
                decision_point="ok",
                context=_ctx_with(hits=1),
                decision="v",
                confidence="auto_complete",
            )
            # Simulate a crash mid-write.
            trace = run_dir / MEMORY_TRACE_FILENAME
            with trace.open("a", encoding="utf-8") as handle:
                handle.write('{"decision_point": "broken", "confidence":')

            entries = read_memory_trace(run_dir)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["decision_point"], "ok")

    def test_writer_writes_atomic_terminated_line(self) -> None:
        """Every successful write ends with exactly one newline."""
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            log_memory_decision(
                run_dir=run_dir,
                decision_point="dp",
                context=_ctx_with(),
                decision="v",
                confidence="auto_complete",
            )
            data = (run_dir / MEMORY_TRACE_FILENAME).read_bytes()
        self.assertTrue(data.endswith(b"\n"))
        # And only one newline at the very end, not blank-line padding.
        self.assertFalse(data.endswith(b"\n\n"))


if __name__ == "__main__":
    unittest.main()
