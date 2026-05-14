"""Tests for ``agentic_swmm.gap_fill.ui_per_gap`` (PRD-GF-L5).

The per-gap UI is the human half of the L5 path. Unlike GF-CORE's
batched form (one form for N gaps), L5 always prompts one judgement
at a time. The contract:

- Render the numbered candidates with summary, tradeoff, evidence
  reference, and the LLM call_id (so the modeller can see the prompt
  dump if they want).
- Capture the modeller's pick (a candidate id) plus an optional
  free-form note.
- ``[defer]`` returns a sentinel that aborts the workflow cleanly so
  the agent reports a deferral rather than picking arbitrarily.
- Non-TTY contexts **always block** — L5 never auto-approves, even
  with ``AISWMM_HITL_AUTO_APPROVE=1`` or
  ``AISWMM_GAP_REGISTRY_ONLY=1`` set.
"""

from __future__ import annotations

import io
import os
import unittest
from unittest import mock

from agentic_swmm.gap_fill.protocol import GapCandidate


def _build_candidates() -> list[GapCandidate]:
    return [
        GapCandidate(
            id="cand_1",
            summary="2026-03-12 — 32 mm / 6 h — high intensity",
            tradeoff="probes surface runoff; weak on infiltration",
        ),
        GapCandidate(
            id="cand_2",
            summary="2026-03-24 — 48 mm / 18 h — moderate / long",
            tradeoff="probes infiltration; weak on impervious peak",
        ),
        GapCandidate(
            id="cand_3",
            summary="2026-03-29 — 18 mm / 3 h — low magnitude",
            tradeoff="probes baseflow; weak on calibration power",
        ),
    ]


class PerGapUIRenderTests(unittest.TestCase):
    """Numbered candidate list + evidence + llm_call_id all appear."""

    def test_renders_numbered_candidates_with_tradeoffs(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import prompt_judgement

        stdout = io.StringIO()
        with mock.patch("builtins.input", side_effect=["1", ""]):
            user_pick, _ = prompt_judgement(
                gap_kind="storm_event_selection",
                candidates=_build_candidates(),
                evidence_ref="06_qa/rainfall_event_summary.json",
                llm_call_id="abc123",
                is_tty=True,
                stdout=stdout,
            )

        rendered = stdout.getvalue()
        # Numbered list with summary + tradeoff for each candidate
        self.assertIn("(1)", rendered)
        self.assertIn("(2)", rendered)
        self.assertIn("(3)", rendered)
        self.assertIn("probes surface runoff", rendered)
        self.assertIn("06_qa/rainfall_event_summary.json", rendered)
        self.assertIn("abc123", rendered)
        # User pick mapped to candidate id
        self.assertEqual(user_pick, "cand_1")


class PerGapUICaptureTests(unittest.TestCase):
    """User pick + free-form note are both captured."""

    def test_captures_pick_and_note(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import prompt_judgement

        stdout = io.StringIO()
        with mock.patch(
            "builtins.input",
            side_effect=["2", "Want infiltration-process calibration."],
        ):
            user_pick, user_note = prompt_judgement(
                gap_kind="storm_event_selection",
                candidates=_build_candidates(),
                evidence_ref="x",
                llm_call_id="x",
                is_tty=True,
                stdout=stdout,
            )

        self.assertEqual(user_pick, "cand_2")
        self.assertEqual(user_note, "Want infiltration-process calibration.")

    def test_blank_note_returns_none(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import prompt_judgement

        stdout = io.StringIO()
        with mock.patch("builtins.input", side_effect=["3", ""]):
            user_pick, user_note = prompt_judgement(
                gap_kind="pour_point",
                candidates=_build_candidates(),
                evidence_ref="x",
                llm_call_id="x",
                is_tty=True,
                stdout=stdout,
            )

        self.assertEqual(user_pick, "cand_3")
        self.assertIsNone(user_note)


class PerGapUIDeferTests(unittest.TestCase):
    """[defer] aborts cleanly via the sentinel return value."""

    def test_defer_returns_sentinel(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import (
            JudgementDeferred,
            prompt_judgement,
        )

        stdout = io.StringIO()
        with mock.patch("builtins.input", side_effect=["defer"]):
            with self.assertRaises(JudgementDeferred):
                prompt_judgement(
                    gap_kind="pour_point",
                    candidates=_build_candidates(),
                    evidence_ref="x",
                    llm_call_id="x",
                    is_tty=True,
                    stdout=stdout,
                )

    def test_invalid_pick_reprompts(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import prompt_judgement

        stdout = io.StringIO()
        # First two inputs are invalid; third selects candidate 1.
        with mock.patch("builtins.input", side_effect=["99", "abc", "1", ""]):
            user_pick, _ = prompt_judgement(
                gap_kind="pour_point",
                candidates=_build_candidates(),
                evidence_ref="x",
                llm_call_id="x",
                is_tty=True,
                stdout=stdout,
            )
        self.assertEqual(user_pick, "cand_1")


class PerGapUINonTTYTests(unittest.TestCase):
    """L5 always blocks under non-TTY — judgement never automated."""

    def test_non_tty_raises_clean_error(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import (
            JudgementBlocked,
            prompt_judgement,
        )

        stdout = io.StringIO()
        with self.assertRaises(JudgementBlocked):
            prompt_judgement(
                gap_kind="pour_point",
                candidates=_build_candidates(),
                evidence_ref="x",
                llm_call_id="x",
                is_tty=False,
                stdout=stdout,
            )

    def test_non_tty_blocks_with_auto_approve_env(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import (
            JudgementBlocked,
            prompt_judgement,
        )

        stdout = io.StringIO()
        # Even with AUTO_APPROVE set, L5 must block.
        with mock.patch.dict(
            os.environ, {"AISWMM_HITL_AUTO_APPROVE": "1"}, clear=False
        ):
            with self.assertRaises(JudgementBlocked):
                prompt_judgement(
                    gap_kind="pour_point",
                    candidates=_build_candidates(),
                    evidence_ref="x",
                    llm_call_id="x",
                    is_tty=False,
                    stdout=stdout,
                )

    def test_non_tty_blocks_with_registry_only_env(self) -> None:
        from agentic_swmm.gap_fill.ui_per_gap import (
            JudgementBlocked,
            prompt_judgement,
        )

        stdout = io.StringIO()
        with mock.patch.dict(
            os.environ, {"AISWMM_GAP_REGISTRY_ONLY": "1"}, clear=False
        ):
            with self.assertRaises(JudgementBlocked):
                prompt_judgement(
                    gap_kind="pour_point",
                    candidates=_build_candidates(),
                    evidence_ref="x",
                    llm_call_id="x",
                    is_tty=False,
                    stdout=stdout,
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
