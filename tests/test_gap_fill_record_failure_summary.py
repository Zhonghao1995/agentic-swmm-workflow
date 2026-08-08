"""Regression tests: a ledger-write failure must not read as an unresolved gap.

Bug (2026-08-08 static sweep, MED): ``_resolve_gap_batch`` folded all
three stage failures (propose, review, record) into a bare ``None``,
so a failure while WRITING the decision ledger — after the user had
already answered every question — was reported as "L1 paths could not
be resolved" / "L3 parameters could not be resolved". The operator
would re-answer the same questions instead of fixing the disk or
permissions problem.

Fix under test: ``_resolve_gap_batch`` returns ``(resolved,
failed_stage)`` and the call sites build the abort summary via
``_gap_abort_summary``, which names the ledger failure distinctly
while keeping the historic wording for genuine resolution failures.
The call still aborts on a record failure (the ledger is the audit
trail; decisions are never backfillable) — only the message changes.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.gap_fill_runtime import (
    _gap_abort_summary,
    _resolve_gap_batch,
)


def _ok_propose(*, signals, run_dir, llm_proposal_fn):
    return [f"proposal-for-{s}" for s in signals]


def _ok_review(proposals, *, tool_name, is_tty):
    return [f"decision-for-{p}" for p in proposals]


class ResolveGapBatchStageTests(unittest.TestCase):
    def test_success_returns_recorded_and_no_stage(self) -> None:
        resolved, failed = _resolve_gap_batch(
            ["sig"],
            tool_name="run_swmm",
            session_dir=None,
            propose_batch=_ok_propose,
            review_batch=_ok_review,
            record_gap_decisions=lambda session_dir, resolved: resolved,
        )
        self.assertEqual(resolved, ["decision-for-proposal-for-sig"])
        self.assertIsNone(failed)

    def test_propose_failure_names_propose(self) -> None:
        def boom(**_kwargs):
            raise RuntimeError("proposer down")

        resolved, failed = _resolve_gap_batch(
            ["sig"],
            tool_name="run_swmm",
            session_dir=None,
            propose_batch=boom,
            review_batch=_ok_review,
            record_gap_decisions=lambda session_dir, resolved: resolved,
        )
        self.assertIsNone(resolved)
        self.assertEqual(failed, "propose")

    def test_review_failure_names_review(self) -> None:
        def rejected(proposals, *, tool_name, is_tty):
            raise RuntimeError("user rejected the batch")

        resolved, failed = _resolve_gap_batch(
            ["sig"],
            tool_name="run_swmm",
            session_dir=None,
            propose_batch=_ok_propose,
            review_batch=rejected,
            record_gap_decisions=lambda session_dir, resolved: resolved,
        )
        self.assertIsNone(resolved)
        self.assertEqual(failed, "review")

    def test_record_failure_names_record(self) -> None:
        """The mined case: the user answered; only persistence failed."""

        def disk_full(session_dir, resolved):
            raise OSError(28, "No space left on device")

        resolved, failed = _resolve_gap_batch(
            ["sig"],
            tool_name="run_swmm",
            session_dir=None,
            propose_batch=_ok_propose,
            review_batch=_ok_review,
            record_gap_decisions=disk_full,
        )
        self.assertIsNone(resolved)
        self.assertEqual(failed, "record")


class AbortSummaryTests(unittest.TestCase):
    def test_record_stage_summary_says_answers_were_provided(self) -> None:
        summary = _gap_abort_summary("record", "L1 paths")
        self.assertIn("answers were provided", summary)
        self.assertIn("decision ledger", summary)
        # The lie the bug produced must be gone.
        self.assertNotIn("could not be resolved", summary)

    def test_resolution_stages_keep_historic_wording(self) -> None:
        self.assertEqual(
            _gap_abort_summary("review", "L1 paths"),
            "gap-fill aborted (L1 paths could not be resolved)",
        )
        self.assertEqual(
            _gap_abort_summary("propose", "L3 parameters"),
            "gap-fill aborted (L3 parameters could not be resolved)",
        )
        # Defensive: unknown/None stage falls back to the generic text.
        self.assertEqual(
            _gap_abort_summary(None, "L1 paths"),
            "gap-fill aborted (L1 paths could not be resolved)",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
