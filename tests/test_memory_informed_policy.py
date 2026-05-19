"""Tests for ``agentic_swmm.agent.memory_informed_policy`` (PRD-07 Phase 3).

The policy is the pure-function slice of disambiguation: given the
user's utterance and the read-side :class:`MemoryContext` snapshot,
it picks one of the four confidence quadrants from PRD-07. No I/O,
no LLM call, no mutation. These tests pin every branch of the
decision tree so the wiring step (planner integration) only has to
worry about flow control, not the rules.

Slices:

1. Empty MemoryContext + low stakes → ``llm``.
2. One parametric hit → ``auto_complete``.
3. ≥2 parametric hits → ``memory_informed``, recency-ranked.
4. Zero hits + high stakes → ``hitl`` with non-empty escalation.
5. Utterance with case-name token matching a hit → ``auto_complete``.
6. Utterance with case-name token *not* in memory → ``llm``.
7. ``stakes`` whitelist enforced.
8. :class:`PolicyDecision` is frozen.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.memory_context import MemoryContext, ParametricRecord
from agentic_swmm.agent.memory_informed_policy import (
    MemoryHITLRequired,
    PolicyDecision,
    VALID_STAKES,
    decide_with_memory,
)


def _hit(
    case_name: str = "saanich-b8",
    run_id: str = "run-1",
    recorded_utc: str | None = None,
) -> ParametricRecord:
    """Construct one ParametricRecord with the fields the policy reads."""
    return ParametricRecord(
        run_id=run_id,
        case_name=case_name,
        recorded_utc=recorded_utc,
    )


class EmptyMemoryTests(unittest.TestCase):
    """Slice 1 — no hits + low stakes defers to LLM."""

    def test_empty_context_low_stakes_returns_llm(self) -> None:
        decision = decide_with_memory(
            "run audit", MemoryContext(), stakes="low"
        )
        self.assertEqual(decision.confidence, "llm")
        self.assertIsNone(decision.resolved_case)
        self.assertEqual(decision.candidates, [])
        self.assertIsNone(decision.escalation)
        # Reasoning must explain *why* we deferred — empty strings
        # would defeat the audit trail.
        self.assertTrue(decision.reasoning.strip())


class SingleHitAutoCompleteTests(unittest.TestCase):
    """Slice 2 — one parametric hit short-circuits to auto_complete."""

    def test_one_hit_auto_completes_to_that_case(self) -> None:
        ctx = MemoryContext(parametric_hits=[_hit("saanich-b8", "r1")])

        decision = decide_with_memory("run audit", ctx)

        self.assertEqual(decision.confidence, "auto_complete")
        self.assertEqual(decision.resolved_case, "saanich-b8")
        self.assertEqual(decision.candidates, ["saanich-b8"])
        self.assertIsNone(decision.escalation)

    def test_one_hit_auto_completes_even_under_high_stakes(self) -> None:
        """High stakes is only blocking when evidence is zero."""
        ctx = MemoryContext(parametric_hits=[_hit("saanich-b8", "r1")])

        decision = decide_with_memory("accept calibration", ctx, stakes="high")

        self.assertEqual(decision.confidence, "auto_complete")
        self.assertEqual(decision.resolved_case, "saanich-b8")


class MultiHitMemoryInformedTests(unittest.TestCase):
    """Slice 3 — multiple candidates rank by recency, propose top-1."""

    def test_three_hits_yield_memory_informed(self) -> None:
        ctx = MemoryContext(
            parametric_hits=[
                _hit("saanich-b8", "r1", recorded_utc="2025-01-01T00:00:00Z"),
                _hit("tecnopolo", "r2", recorded_utc="2025-06-01T00:00:00Z"),
                _hit("Todcreek", "r3", recorded_utc="2025-03-01T00:00:00Z"),
            ]
        )

        decision = decide_with_memory("run audit", ctx)

        self.assertEqual(decision.confidence, "memory_informed")
        # Most recent first.
        self.assertEqual(decision.candidates[0], "tecnopolo")
        self.assertEqual(decision.resolved_case, "tecnopolo")
        # All three case names should appear.
        self.assertEqual(
            set(decision.candidates), {"saanich-b8", "tecnopolo", "Todcreek"}
        )

    def test_unannotated_records_sort_to_end(self) -> None:
        """Records lacking recorded_utc must not beat a stamped one."""
        ctx = MemoryContext(
            parametric_hits=[
                _hit("unstamped-a", "r1", recorded_utc=None),
                _hit("stamped-b", "r2", recorded_utc="2024-12-31T00:00:00Z"),
                _hit("unstamped-c", "r3", recorded_utc=""),
            ]
        )

        decision = decide_with_memory("run audit", ctx)

        self.assertEqual(decision.confidence, "memory_informed")
        self.assertEqual(decision.candidates[0], "stamped-b")


class HighStakesEscalationTests(unittest.TestCase):
    """Slice 4 — high stakes + zero evidence raises hitl."""

    def test_zero_hits_high_stakes_returns_hitl(self) -> None:
        decision = decide_with_memory(
            "accept calibration", MemoryContext(), stakes="high"
        )
        self.assertEqual(decision.confidence, "hitl")
        self.assertIsNone(decision.resolved_case)
        self.assertEqual(decision.candidates, [])
        self.assertIsNotNone(decision.escalation)
        self.assertTrue((decision.escalation or "").strip())
        # The reasoning must capture the cause so the audit log
        # contains enough information for a reviewer.
        self.assertIn("high-stakes", decision.reasoning)

    def test_memory_hitl_required_is_an_exception(self) -> None:
        """Wiring code raises MemoryHITLRequired; smoke its API."""
        with self.assertRaises(MemoryHITLRequired) as cm:
            raise MemoryHITLRequired("please confirm")
        self.assertIn("please confirm", str(cm.exception))


class ExplicitCaseNameTokenTests(unittest.TestCase):
    """Slices 5+6 — explicit case-name tokens auto-resolve or defer."""

    def test_explicit_token_matches_among_many_hits(self) -> None:
        ctx = MemoryContext(
            parametric_hits=[
                _hit("saanich-b8", "r1", recorded_utc="2025-01-01T00:00:00Z"),
                _hit("tecnopolo", "r2", recorded_utc="2025-06-01T00:00:00Z"),
            ]
        )

        decision = decide_with_memory(
            "run audit on tecnopolo", ctx
        )

        self.assertEqual(decision.confidence, "auto_complete")
        self.assertEqual(decision.resolved_case, "tecnopolo")

    def test_explicit_token_match_case_insensitive_and_punctuation(
        self,
    ) -> None:
        """User typing 'Saanich B8' must match case_name 'saanich-b8'."""
        ctx = MemoryContext(
            parametric_hits=[
                _hit("saanich-b8", "r1", recorded_utc="2025-01-01T00:00:00Z"),
                _hit("tecnopolo", "r2", recorded_utc="2025-06-01T00:00:00Z"),
            ]
        )

        decision = decide_with_memory(
            "please run audit on Saanichb8 next", ctx
        )

        self.assertEqual(decision.confidence, "auto_complete")
        self.assertEqual(decision.resolved_case, "saanich-b8")

    def test_explicit_token_not_in_memory_defers_to_llm(self) -> None:
        ctx = MemoryContext(
            parametric_hits=[
                _hit("saanich-b8", "r1", recorded_utc="2025-01-01T00:00:00Z"),
                _hit("tecnopolo", "r2", recorded_utc="2025-06-01T00:00:00Z"),
            ]
        )

        decision = decide_with_memory(
            "run audit on Brisbane2030", ctx
        )

        # Memory has records but the typed token names a different
        # case — fall through to LLM/keyword fallback.
        self.assertEqual(decision.confidence, "llm")
        self.assertIsNone(decision.resolved_case)
        # Candidates list reflects what memory does know about so
        # the caller can surface them if it wants.
        self.assertEqual(
            set(decision.candidates), {"saanich-b8", "tecnopolo"}
        )

    def test_explicit_token_only_zero_hits_defers_to_llm(self) -> None:
        decision = decide_with_memory(
            "run audit on Brisbane2030", MemoryContext()
        )
        self.assertEqual(decision.confidence, "llm")
        self.assertIsNone(decision.resolved_case)


class StakesValidationTests(unittest.TestCase):
    """Slice 7 — stakes must be one of the whitelisted labels."""

    def test_valid_stakes_set(self) -> None:
        self.assertEqual(set(VALID_STAKES), {"low", "high"})

    def test_invalid_stakes_raises(self) -> None:
        with self.assertRaises(ValueError) as cm:
            decide_with_memory(
                "run audit",
                MemoryContext(),
                stakes="medium",  # type: ignore[arg-type]
            )
        self.assertIn("stakes", str(cm.exception))


class PolicyDecisionDataclassTests(unittest.TestCase):
    """Slice 8 — PolicyDecision is frozen and the API stable."""

    def test_decision_is_frozen(self) -> None:
        decision = decide_with_memory("run audit", MemoryContext())
        with self.assertRaises(Exception):
            # Frozen dataclasses raise FrozenInstanceError (subclass
            # of AttributeError) on attribute assignment.
            decision.confidence = "auto_complete"  # type: ignore[misc]

    def test_decision_default_candidates_is_empty_list(self) -> None:
        d = PolicyDecision(confidence="llm", resolved_case=None)
        self.assertEqual(d.candidates, [])
        self.assertEqual(d.reasoning, "")
        self.assertIsNone(d.escalation)


class _FakeRec:
    """Minimal stand-in for ``TransferRecommendation`` for policy tests.

    The policy only reads ``source_case`` and ``similarity`` so this
    bare-bones class is enough; it also keeps the policy test file
    free of an import on the cross-watershed module (the policy
    contract is "anything with those attributes works").
    """

    def __init__(self, source_case: str, similarity: float) -> None:
        self.source_case = source_case
        self.similarity = similarity


class CrossWatershedTransferIntegrationTests(unittest.TestCase):
    """Phase 5 — transfer_lookup is consulted only when relevant."""

    def test_calibrate_intent_no_history_with_recs_returns_memory_informed(
        self,
    ) -> None:
        recs = [_FakeRec("saanich-b8", 0.85)]
        decision = decide_with_memory(
            "calibrate this new model",
            MemoryContext(),
            transfer_lookup=lambda: recs,
        )
        self.assertEqual(decision.confidence, "memory_informed")
        self.assertIn("saanich-b8", decision.reasoning)
        self.assertIn("0.85", decision.reasoning)
        # ``candidates`` reflects the source-case ordering for the UI.
        self.assertEqual(decision.candidates, ["saanich-b8"])

    def test_calibration_keyword_also_triggers_transfer(self) -> None:
        recs = [_FakeRec("saanich-b8", 0.9)]
        decision = decide_with_memory(
            "run a calibration on the new INP",
            MemoryContext(),
            transfer_lookup=lambda: recs,
        )
        self.assertEqual(decision.confidence, "memory_informed")

    def test_tune_keyword_also_triggers_transfer(self) -> None:
        recs = [_FakeRec("saanich-b8", 0.7)]
        decision = decide_with_memory(
            "tune the model parameters",
            MemoryContext(),
            transfer_lookup=lambda: recs,
        )
        self.assertEqual(decision.confidence, "memory_informed")

    def test_no_candidates_anywhere_with_high_stakes_returns_hitl(self) -> None:
        decision = decide_with_memory(
            "calibrate this model",
            MemoryContext(),
            stakes="high",
            transfer_lookup=lambda: [],
        )
        self.assertEqual(decision.confidence, "hitl")
        self.assertIsNotNone(decision.escalation)
        self.assertTrue((decision.escalation or "").strip())

    def test_no_candidates_low_stakes_returns_llm(self) -> None:
        decision = decide_with_memory(
            "calibrate this model",
            MemoryContext(),
            stakes="low",
            transfer_lookup=lambda: [],
        )
        self.assertEqual(decision.confidence, "llm")
        self.assertIsNone(decision.escalation)

    def test_non_calibration_intent_skips_transfer_lookup(self) -> None:
        """``run audit`` must not trigger transfer."""
        # The lookup raises if invoked — proves it's never called.
        def _explode() -> list[_FakeRec]:  # pragma: no cover - must not run
            raise AssertionError("transfer_lookup must not fire for audit")

        decision = decide_with_memory(
            "run audit",
            MemoryContext(),
            transfer_lookup=_explode,
        )
        # Empty context + no calibration intent → standard llm path.
        self.assertEqual(decision.confidence, "llm")

    def test_existing_hits_skip_transfer_lookup(self) -> None:
        """If the case already has parametric history, transfer is skipped."""

        def _explode() -> list[_FakeRec]:  # pragma: no cover - must not run
            raise AssertionError("transfer_lookup must not fire when hits exist")

        ctx = MemoryContext(parametric_hits=[_hit("saanich-b8", "r1")])
        decision = decide_with_memory(
            "calibrate saanich-b8",
            ctx,
            transfer_lookup=_explode,
        )
        # Existing case wins on auto_complete.
        self.assertEqual(decision.confidence, "auto_complete")

    def test_misbehaving_lookup_swallowed(self) -> None:
        """A lookup that raises must not crash the policy."""

        def _broken() -> list[_FakeRec]:
            raise RuntimeError("disk full")

        decision = decide_with_memory(
            "calibrate this model",
            MemoryContext(),
            transfer_lookup=_broken,
        )
        # Falls through as if no recs were returned.
        self.assertEqual(decision.confidence, "llm")

    def test_no_transfer_lookup_keeps_legacy_behaviour(self) -> None:
        """Omitting ``transfer_lookup`` reproduces the pre-Phase-5 path."""
        decision = decide_with_memory(
            "calibrate this model",
            MemoryContext(),
            stakes="high",
        )
        # Without a lookup the high-stakes + zero-hits branch fires.
        self.assertEqual(decision.confidence, "hitl")


if __name__ == "__main__":
    unittest.main()
