"""Tests for ``agentic_swmm.gap_fill.proposer`` (PRD-GF-CORE).

The proposer is the layered decision pipeline:

1. **Registry lookup** in ``defaults_table.yaml``. A hit produces a
   ``source=registry`` proposal with ``confidence=HIGH`` and no LLM
   call recorded.
2. **LLM-grounded** if the registry misses and ``AISWMM_GAP_REGISTRY_ONLY``
   is unset. The proposer calls ``record_llm_call(caller=
   "gap_fill.proposer", ...)`` with a tightly-scoped prompt instructing
   the LLM to propose one value with citation.
3. **Human fallthrough** if the LLM response is low-confidence /
   unparseable.

Environment-driven branches:

- ``AISWMM_GAP_REGISTRY_ONLY=1`` — L3 lookups stay registry-only. Miss
  raises a clean error (``GapFillRegistryOnlyMiss``) so CI sees a
  loud failure instead of guessing.
- ``AISWMM_HITL_AUTO_APPROVE=1`` — the proposer's value (registry or
  LLM-grounded) is auto-accepted via ``decided_by=auto_approve`` and
  the auto-approval is logged loudly to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.gap_fill.protocol import GapSignal, ProposerInfo
from agentic_swmm.gap_fill.proposer import (
    GapFillRegistryOnlyMiss,
    LLMProposalFn,
    LLMProposal,
    propose,
)


def _l3_signal(field: str = "manning_n_imperv") -> GapSignal:
    return GapSignal(
        gap_id="gap-test",
        severity="L3",
        kind="param_value",
        field=field,
        context={"tool": "build_inp"},
    )


def _l1_signal(field: str = "rainfall_file") -> GapSignal:
    return GapSignal(
        gap_id="gap-test-l1",
        severity="L1",
        kind="file_path",
        field=field,
        context={"tool": "build_inp"},
    )


@dataclass
class _FakeResponse:
    """Stand-in for a provider response — ``record_llm_call`` reads
    ``.text``, ``.tool_calls``, ``.model``, ``.usage``.
    """

    text: str = ""
    model: str = "claude-opus-4-7"
    tool_calls: list = None  # noqa: RUF013
    usage: object = None

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class RegistryHitTests(unittest.TestCase):
    def test_registry_hit_returns_high_confidence_no_llm(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            llm_calls = []

            def _llm_should_not_be_called(*_args, **_kwargs) -> LLMProposal:
                llm_calls.append(("called",))
                raise AssertionError("LLM should not be called on registry hit")

            decision = propose(
                signal=_l3_signal("manning_n_paved"),
                run_dir=run_dir,
                llm_proposal_fn=_llm_should_not_be_called,
            )
            self.assertEqual(decision.proposer.source, "registry")
            self.assertEqual(decision.proposer.confidence, "HIGH")
            self.assertEqual(decision.proposed_value, 0.013)
            self.assertEqual(decision.final_value, 0.013)
            self.assertIsNotNone(decision.proposer.registry_ref)
            self.assertIn("manning_n_paved", decision.proposer.registry_ref)
            # No LLM call recorded — the audit ledger must not exist.
            self.assertFalse((run_dir / "09_audit" / "llm_calls.jsonl").exists())
            self.assertEqual(llm_calls, [])

    def test_registry_alias_lookup_falls_back(self) -> None:
        """Field names not in the registry should fall through to LLM."""

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            llm_called = []

            def _llm(*, signal, run_dir):
                llm_called.append(signal.field)
                return LLMProposal(
                    value=0.025,
                    literature_ref="textbook X",
                    confidence="HIGH",
                    call_id="call-1",
                )

            decision = propose(
                signal=_l3_signal("unknown_parameter_xyz"),
                run_dir=run_dir,
                llm_proposal_fn=_llm,
            )
            self.assertEqual(decision.proposer.source, "llm_grounded")
            self.assertEqual(decision.proposed_value, 0.025)
            self.assertEqual(llm_called, ["unknown_parameter_xyz"])
            self.assertEqual(decision.proposer.llm_call_id, "call-1")


class LLMGroundedTests(unittest.TestCase):
    def test_high_confidence_llm_returns_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            def _llm(*, signal, run_dir):
                return LLMProposal(
                    value=0.018,
                    literature_ref="EPA SWMM Manual Table 8-3",
                    confidence="HIGH",
                    call_id="llm-abc",
                )

            decision = propose(
                signal=_l3_signal("unknown_field"),
                run_dir=run_dir,
                llm_proposal_fn=_llm,
            )
            self.assertEqual(decision.proposer.source, "llm_grounded")
            self.assertEqual(decision.proposer.confidence, "HIGH")
            self.assertEqual(decision.proposed_value, 0.018)
            self.assertEqual(
                decision.proposer.literature_ref, "EPA SWMM Manual Table 8-3"
            )
            self.assertEqual(decision.proposer.llm_call_id, "llm-abc")

    def test_low_confidence_llm_falls_through_to_human(self) -> None:
        """An LLM response with no grounding becomes a human-required gap.

        The proposer returns ``source=human``, ``proposed_value=None``;
        the UI is responsible for collecting the user's value.
        """

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            def _llm(*, signal, run_dir):
                return LLMProposal(
                    value=None,
                    literature_ref=None,
                    confidence="LOW",
                    call_id="llm-low",
                )

            decision = propose(
                signal=_l3_signal("unknown_field"),
                run_dir=run_dir,
                llm_proposal_fn=_llm,
            )
            self.assertEqual(decision.proposer.source, "human")
            self.assertEqual(decision.proposer.confidence, "LOW")
            self.assertIsNone(decision.proposed_value)


class L1PathProposerTests(unittest.TestCase):
    def test_l1_skips_registry_and_returns_human(self) -> None:
        """L1 file-path gaps always defer to the user — paths cannot be
        proposed from a textbook table.
        """

        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            def _llm(*, signal, run_dir):  # pragma: no cover - guarded
                raise AssertionError("LLM should not be called for L1 paths")

            decision = propose(
                signal=_l1_signal(),
                run_dir=run_dir,
                llm_proposal_fn=_llm,
            )
            self.assertEqual(decision.proposer.source, "human")
            self.assertIsNone(decision.proposed_value)


class RegistryOnlyModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("AISWMM_GAP_REGISTRY_ONLY")
        os.environ["AISWMM_GAP_REGISTRY_ONLY"] = "1"

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("AISWMM_GAP_REGISTRY_ONLY", None)
        else:
            os.environ["AISWMM_GAP_REGISTRY_ONLY"] = self._saved

    def test_registry_hit_succeeds(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decision = propose(
                signal=_l3_signal("manning_n_paved"),
                run_dir=run_dir,
                llm_proposal_fn=None,
            )
            self.assertEqual(decision.proposer.source, "registry")
            self.assertEqual(decision.decided_by, "auto_registry")

    def test_registry_miss_raises_clean_error(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            with self.assertRaises(GapFillRegistryOnlyMiss) as cm:
                propose(
                    signal=_l3_signal("unknown_field"),
                    run_dir=run_dir,
                    llm_proposal_fn=None,
                )
            self.assertIn("unknown_field", str(cm.exception))


class AutoApproveModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("AISWMM_HITL_AUTO_APPROVE")
        os.environ["AISWMM_HITL_AUTO_APPROVE"] = "1"
        # Ensure we are NOT in registry-only mode for this test.
        self._saved2 = os.environ.get("AISWMM_GAP_REGISTRY_ONLY")
        os.environ.pop("AISWMM_GAP_REGISTRY_ONLY", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("AISWMM_HITL_AUTO_APPROVE", None)
        else:
            os.environ["AISWMM_HITL_AUTO_APPROVE"] = self._saved
        if self._saved2 is not None:
            os.environ["AISWMM_GAP_REGISTRY_ONLY"] = self._saved2

    def test_auto_approve_registry_hit(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decision = propose(
                signal=_l3_signal("manning_n_paved"),
                run_dir=run_dir,
                llm_proposal_fn=None,
            )
            self.assertEqual(decision.decided_by, "auto_approve")
            self.assertEqual(decision.final_value, 0.013)

    def test_auto_approve_llm_proposal_with_loud_log(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            def _llm(*, signal, run_dir):
                return LLMProposal(
                    value=0.022,
                    literature_ref="textbook Y",
                    confidence="HIGH",
                    call_id="llm-id",
                )

            from io import StringIO

            buf = StringIO()
            with mock.patch.object(sys, "stderr", buf):
                decision = propose(
                    signal=_l3_signal("unknown_field_x"),
                    run_dir=run_dir,
                    llm_proposal_fn=_llm,
                )
            self.assertEqual(decision.decided_by, "auto_approve")
            self.assertEqual(decision.final_value, 0.022)
            self.assertIn("AUTO_APPROVE", buf.getvalue())


class RegistryAliasTests(unittest.TestCase):
    """The registry uses canonical names like ``manning_n_paved``; tool
    args use names like ``manning_n_imperv``. The proposer maintains a
    small alias map so common SWMM args resolve to the right entry.
    """

    def test_manning_n_imperv_resolves_to_paved(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            decision = propose(
                signal=_l3_signal("manning_n_imperv"),
                run_dir=run_dir,
                llm_proposal_fn=None,
            )
            self.assertEqual(decision.proposer.source, "registry")
            self.assertEqual(decision.proposed_value, 0.013)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
