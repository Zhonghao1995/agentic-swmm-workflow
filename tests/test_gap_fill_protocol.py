"""Tests for ``agentic_swmm.gap_fill.protocol`` (PRD-GF-CORE).

The protocol module owns the canonical wire formats that travel between
tools, the runtime, the proposer, the UI, and the recorder. Two
invariants this test suite locks in:

1. **Round-trip stability** — ``to_dict`` followed by ``from_dict``
   reconstructs an equal object. Tools and the recorder hand payloads
   off through JSON, so a missing field in the serialiser would silently
   drop data.
2. **Defensive validation** — malformed payloads (missing required
   keys, wrong severity, wrong kind) raise ``ValueError`` at parse
   time rather than producing a half-built dataclass that later code
   would misinterpret.
"""

from __future__ import annotations

import unittest

from agentic_swmm.gap_fill.protocol import (
    GapBatch,
    GapDecision,
    GapSignal,
    ProposerInfo,
    new_decision_id,
    new_gap_id,
)


class GapSignalRoundTripTests(unittest.TestCase):
    def test_minimal_signal_round_trips(self) -> None:
        signal = GapSignal(
            gap_id="gap-1",
            severity="L1",
            kind="file_path",
            field="rainfall_file",
            context={"workflow": "swmm-end-to-end", "step": "build", "tool": "build_inp"},
        )
        payload = signal.to_dict()
        restored = GapSignal.from_dict(payload)
        self.assertEqual(signal, restored)

    def test_signal_with_suggestion_round_trips(self) -> None:
        signal = GapSignal(
            gap_id="gap-2",
            severity="L3",
            kind="param_value",
            field="manning_n_imperv",
            context={"tool": "build_inp"},
            suggestion={"hint": "paved surface"},
        )
        payload = signal.to_dict()
        restored = GapSignal.from_dict(payload)
        self.assertEqual(signal, restored)

    def test_bad_severity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GapSignal.from_dict(
                {
                    "gap_id": "g",
                    "severity": "L4",
                    "kind": "file_path",
                    "field": "x",
                    "context": {},
                }
            )

    def test_bad_kind_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GapSignal.from_dict(
                {
                    "gap_id": "g",
                    "severity": "L1",
                    "kind": "weird_thing",
                    "field": "x",
                    "context": {},
                }
            )

    def test_missing_field_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GapSignal.from_dict(
                {"gap_id": "g", "severity": "L1", "kind": "file_path", "context": {}}
            )

    def test_severity_kind_alignment_enforced(self) -> None:
        # L1 must be file_path; L3 must be param_value.
        with self.assertRaises(ValueError):
            GapSignal(
                gap_id="g",
                severity="L1",
                kind="param_value",
                field="x",
                context={},
            )
        with self.assertRaises(ValueError):
            GapSignal(
                gap_id="g",
                severity="L3",
                kind="file_path",
                field="x",
                context={},
            )


class GapDecisionRoundTripTests(unittest.TestCase):
    def test_decision_round_trips(self) -> None:
        decision = GapDecision(
            decision_id="dec-1",
            gap_id="gap-1",
            severity="L3",
            field="manning_n_imperv",
            proposer=ProposerInfo(
                source="registry",
                registry_ref="defaults_table.yaml#manning_n_paved",
                literature_ref="EPA SWMM 5 Reference Manual, Table 8-1",
                confidence="HIGH",
                llm_call_id=None,
            ),
            proposed_value=0.013,
            final_value=0.013,
            proposer_overridden=False,
            decided_by="human",
            decided_at="2026-05-14T10:00:00Z",
            resume_mode="tool_retry",
            human_decisions_ref="09_audit/experiment_provenance.json#human_decisions[0]",
        )
        payload = decision.to_dict()
        restored = GapDecision.from_dict(payload)
        self.assertEqual(decision, restored)

    def test_decision_rejects_bad_source(self) -> None:
        with self.assertRaises(ValueError):
            GapDecision(
                decision_id="d",
                gap_id="g",
                severity="L3",
                field="x",
                proposer=ProposerInfo(source="oracle", confidence="HIGH"),
                proposed_value="y",
                final_value="y",
                proposer_overridden=False,
                decided_by="human",
                decided_at="now",
                resume_mode="tool_retry",
                human_decisions_ref=None,
            )

    def test_decision_rejects_bad_decided_by(self) -> None:
        with self.assertRaises(ValueError):
            GapDecision(
                decision_id="d",
                gap_id="g",
                severity="L1",
                field="x",
                proposer=ProposerInfo(source="human", confidence="HIGH"),
                proposed_value=None,
                final_value="a",
                proposer_overridden=False,
                decided_by="machine",
                decided_at="now",
                resume_mode="tool_retry",
                human_decisions_ref=None,
            )


class GapBatchTests(unittest.TestCase):
    def test_batch_round_trips(self) -> None:
        s1 = GapSignal(
            gap_id="g1",
            severity="L1",
            kind="file_path",
            field="rainfall_file",
            context={"tool": "build_inp"},
        )
        s2 = GapSignal(
            gap_id="g2",
            severity="L3",
            kind="param_value",
            field="manning_n_imperv",
            context={"tool": "build_inp"},
        )
        batch = GapBatch(tool="build_inp", signals=[s1, s2])
        payload = batch.to_dict()
        restored = GapBatch.from_dict(payload)
        self.assertEqual(batch, restored)
        self.assertEqual(len(restored.signals), 2)

    def test_empty_batch_allowed(self) -> None:
        batch = GapBatch(tool="x", signals=[])
        self.assertEqual(batch.signals, [])


class IdGeneratorTests(unittest.TestCase):
    def test_new_gap_id_unique(self) -> None:
        a = new_gap_id()
        b = new_gap_id()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("gap-"))

    def test_new_decision_id_unique(self) -> None:
        a = new_decision_id()
        b = new_decision_id()
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("dec-"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
