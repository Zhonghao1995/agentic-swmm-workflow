"""Tests for ``agentic_swmm.gap_fill.ui`` (PRD-GF-CORE).

The UI is a batched accept/edit/reject form for the L1+L3 mix that
a single tool call produces. We test three control flows:

1. **TTY accept** — every gap is accepted; ``final_value`` mirrors
   ``proposed_value`` and ``proposer_overridden`` is False.
2. **TTY edit** — at least one gap is edited; ``final_value`` carries
   the new value, ``proposer_overridden`` is True.
3. **TTY reject** — the form raises :class:`GapFillRejected` so the
   runtime can abort the workflow cleanly.

Non-TTY paths honour the env-var matrix from the PRD: ``AISWMM_GAP_REGISTRY_ONLY``
auto-accepts registry hits and raises on L1 paths; ``AISWMM_HITL_AUTO_APPROVE``
auto-accepts any non-human-required proposal.
"""

from __future__ import annotations

import os
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.gap_fill.protocol import (
    GapDecision,
    ProposerInfo,
    new_decision_id,
    new_gap_id,
)
from agentic_swmm.gap_fill.ui import (
    GapFillNonInteractive,
    GapFillRejected,
    review_batch,
)


def _registry_decision(field: str = "manning_n_imperv", value: float = 0.013) -> GapDecision:
    return GapDecision(
        decision_id=new_decision_id(),
        gap_id=new_gap_id(),
        severity="L3",
        field=field,
        proposer=ProposerInfo(
            source="registry",
            confidence="HIGH",
            registry_ref="defaults_table.yaml#manning_n_paved",
            literature_ref="EPA SWMM 5 Reference Manual",
        ),
        proposed_value=value,
        final_value=value,
        proposer_overridden=False,
        decided_by="human",
        decided_at="2026-05-14T10:00:00Z",
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


def _human_l1_decision(field: str = "rainfall_file") -> GapDecision:
    return GapDecision(
        decision_id=new_decision_id(),
        gap_id=new_gap_id(),
        severity="L1",
        field=field,
        proposer=ProposerInfo(source="human", confidence="HIGH"),
        proposed_value=None,
        final_value=None,
        proposer_overridden=False,
        decided_by="human",
        decided_at="2026-05-14T10:00:00Z",
        resume_mode="tool_retry",
        human_decisions_ref=None,
    )


class TTYAcceptPathTests(unittest.TestCase):
    def test_accept_all_keeps_proposed_values(self) -> None:
        decisions = [_registry_decision()]
        # input: "a" → accept
        with mock.patch("builtins.input", side_effect=["a"]):
            reviewed = review_batch(
                decisions,
                tool_name="build_inp",
                is_tty=True,
                stdout=StringIO(),
            )
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0].final_value, 0.013)
        self.assertFalse(reviewed[0].proposer_overridden)
        self.assertEqual(reviewed[0].decided_by, "human")

    def test_edit_overrides_value(self) -> None:
        decisions = [_registry_decision()]
        # input: "e" → edit, then "0.020" → new value
        with mock.patch("builtins.input", side_effect=["e", "0.020"]):
            reviewed = review_batch(
                decisions,
                tool_name="build_inp",
                is_tty=True,
                stdout=StringIO(),
            )
        self.assertEqual(reviewed[0].final_value, "0.020")
        self.assertTrue(reviewed[0].proposer_overridden)

    def test_reject_raises(self) -> None:
        decisions = [_registry_decision()]
        with mock.patch("builtins.input", side_effect=["r"]):
            with self.assertRaises(GapFillRejected):
                review_batch(
                    decisions,
                    tool_name="build_inp",
                    is_tty=True,
                    stdout=StringIO(),
                )

    def test_l1_path_collects_user_value(self) -> None:
        decisions = [_human_l1_decision()]
        # No proposed_value → UI prompts for the path directly.
        with mock.patch("builtins.input", side_effect=["/cases/case-a/rain.csv"]):
            reviewed = review_batch(
                decisions,
                tool_name="build_inp",
                is_tty=True,
                stdout=StringIO(),
            )
        self.assertEqual(reviewed[0].final_value, "/cases/case-a/rain.csv")
        self.assertEqual(reviewed[0].decided_by, "human")

    def test_batched_two_gaps_one_form(self) -> None:
        decisions = [_human_l1_decision("rain_file"), _registry_decision()]
        # First the L1 path, then "a" for accept on the L3.
        with mock.patch(
            "builtins.input", side_effect=["/cases/case-a/rain.csv", "a"]
        ):
            reviewed = review_batch(
                decisions,
                tool_name="build_inp",
                is_tty=True,
                stdout=StringIO(),
            )
        self.assertEqual(len(reviewed), 2)
        self.assertEqual(reviewed[0].final_value, "/cases/case-a/rain.csv")
        self.assertEqual(reviewed[1].final_value, 0.013)


class NonTTYEnvVarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_auto = os.environ.get("AISWMM_HITL_AUTO_APPROVE")
        self._saved_reg = os.environ.get("AISWMM_GAP_REGISTRY_ONLY")
        os.environ.pop("AISWMM_HITL_AUTO_APPROVE", None)
        os.environ.pop("AISWMM_GAP_REGISTRY_ONLY", None)

    def tearDown(self) -> None:
        for var, saved in (
            ("AISWMM_HITL_AUTO_APPROVE", self._saved_auto),
            ("AISWMM_GAP_REGISTRY_ONLY", self._saved_reg),
        ):
            if saved is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = saved

    def test_non_tty_no_env_raises(self) -> None:
        decisions = [_registry_decision()]
        with self.assertRaises(GapFillNonInteractive):
            review_batch(
                decisions,
                tool_name="build_inp",
                is_tty=False,
                stdout=StringIO(),
            )

    def test_non_tty_auto_approve_accepts_registry(self) -> None:
        os.environ["AISWMM_HITL_AUTO_APPROVE"] = "1"
        decisions = [_registry_decision()]
        # Mark the decision as already auto-approved (proposer's job)
        decisions[0] = GapDecision(
            **{
                **decisions[0].to_dict(),
                "proposer": decisions[0].proposer.to_dict(),
                "decided_by": "auto_approve",
            }
        ) if False else GapDecision(
            decision_id=decisions[0].decision_id,
            gap_id=decisions[0].gap_id,
            severity=decisions[0].severity,
            field=decisions[0].field,
            proposer=decisions[0].proposer,
            proposed_value=decisions[0].proposed_value,
            final_value=decisions[0].final_value,
            proposer_overridden=decisions[0].proposer_overridden,
            decided_by="auto_approve",
            decided_at=decisions[0].decided_at,
            resume_mode=decisions[0].resume_mode,
            human_decisions_ref=decisions[0].human_decisions_ref,
        )
        reviewed = review_batch(
            decisions,
            tool_name="build_inp",
            is_tty=False,
            stdout=StringIO(),
        )
        self.assertEqual(reviewed[0].final_value, 0.013)
        self.assertEqual(reviewed[0].decided_by, "auto_approve")

    def test_non_tty_l1_with_auto_approve_still_blocks(self) -> None:
        """L1 paths cannot be auto-approved (PRD failure-path matrix)."""

        os.environ["AISWMM_HITL_AUTO_APPROVE"] = "1"
        decisions = [_human_l1_decision()]
        with self.assertRaises(GapFillNonInteractive):
            review_batch(
                decisions,
                tool_name="build_inp",
                is_tty=False,
                stdout=StringIO(),
            )

    def test_non_tty_registry_only_accepts_registry_hit(self) -> None:
        os.environ["AISWMM_GAP_REGISTRY_ONLY"] = "1"
        decisions = [
            GapDecision(
                decision_id=new_decision_id(),
                gap_id=new_gap_id(),
                severity="L3",
                field="manning_n_imperv",
                proposer=ProposerInfo(
                    source="registry",
                    confidence="HIGH",
                    registry_ref="defaults_table.yaml#manning_n_paved",
                    literature_ref="ref",
                ),
                proposed_value=0.013,
                final_value=0.013,
                proposer_overridden=False,
                decided_by="auto_registry",
                decided_at="2026-05-14T10:00:00Z",
                resume_mode="tool_retry",
                human_decisions_ref=None,
            )
        ]
        reviewed = review_batch(
            decisions,
            tool_name="build_inp",
            is_tty=False,
            stdout=StringIO(),
        )
        self.assertEqual(reviewed[0].final_value, 0.013)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
