"""L5 always blocks in headless contexts (PRD-GF-L5).

The PRD-GF-L5 failure-path matrix says: L5 is the one severity where
no env var unlocks an automation path. Subjective judgement cannot
come from a registry table, and it cannot be auto-approved. Both
``AISWMM_GAP_REGISTRY_ONLY`` and ``AISWMM_HITL_AUTO_APPROVE`` are
explicitly ignored for the L5 path — the per-gap UI raises
:class:`JudgementBlocked` in non-TTY contexts regardless of those
env vars.

This is the paper-governance story: judgement is never automated.
"""

from __future__ import annotations

import os
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
            id="cand_1", summary="A", tradeoff="cheaper but lower fidelity"
        ),
        GapCandidate(
            id="cand_2", summary="B", tradeoff="costlier but higher fidelity"
        ),
    ]


def _make_call() -> ToolCall:
    return ToolCall(
        "request_gap_judgement",
        {
            "gap_kind": "pour_point",
            "context": {"workflow": "swmm-gis"},
            "evidence_ref": "06_qa/x.json",
        },
    )


class L5BlocksWhenRegistryOnly(unittest.TestCase):
    def test_blocks_under_gap_registry_only(self) -> None:
        registry = AgentToolRegistry()
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            with mock.patch.dict(
                os.environ,
                {"AISWMM_GAP_REGISTRY_ONLY": "1"},
                clear=False,
            ), mock.patch(
                "agentic_swmm.gap_fill.llm_enumerator.enumerate_candidates",
                return_value=(_stub_candidates(), "cid"),
            ), mock.patch(
                # Force non-TTY in the handler's TTY probe.
                "agentic_swmm.agent.tool_registry._is_tty_for_l5",
                return_value=False,
            ):
                result = registry.execute(_make_call(), session_dir)

            self.assertFalse(result.get("ok"))
            summary = (result.get("summary") or "").lower()
            self.assertIn("block", summary)
            # Most importantly: nothing got recorded — judgement was
            # refused, not silently auto-resolved.
            ledger = session_dir / "09_audit" / "gap_decisions.json"
            self.assertFalse(
                ledger.is_file(),
                "L5 must not record a decision when blocked",
            )


class L5BlocksWhenAutoApprove(unittest.TestCase):
    def test_blocks_under_hitl_auto_approve(self) -> None:
        registry = AgentToolRegistry()
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            with mock.patch.dict(
                os.environ,
                {"AISWMM_HITL_AUTO_APPROVE": "1"},
                clear=False,
            ), mock.patch(
                "agentic_swmm.gap_fill.llm_enumerator.enumerate_candidates",
                return_value=(_stub_candidates(), "cid"),
            ), mock.patch(
                "agentic_swmm.agent.tool_registry._is_tty_for_l5",
                return_value=False,
            ):
                result = registry.execute(_make_call(), session_dir)

            self.assertFalse(result.get("ok"))
            self.assertIn("block", (result.get("summary") or "").lower())
            ledger = session_dir / "09_audit" / "gap_decisions.json"
            self.assertFalse(
                ledger.is_file(),
                "L5 must not record a decision when blocked",
            )


class L5BlocksWhenBothEnvVarsSet(unittest.TestCase):
    def test_blocks_under_both_env_vars(self) -> None:
        registry = AgentToolRegistry()
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            with mock.patch.dict(
                os.environ,
                {
                    "AISWMM_HITL_AUTO_APPROVE": "1",
                    "AISWMM_GAP_REGISTRY_ONLY": "1",
                },
                clear=False,
            ), mock.patch(
                "agentic_swmm.gap_fill.llm_enumerator.enumerate_candidates",
                return_value=(_stub_candidates(), "cid"),
            ), mock.patch(
                "agentic_swmm.agent.tool_registry._is_tty_for_l5",
                return_value=False,
            ):
                result = registry.execute(_make_call(), session_dir)

            self.assertFalse(result.get("ok"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
