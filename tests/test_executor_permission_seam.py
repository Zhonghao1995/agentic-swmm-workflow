"""Issue #193 item 2: executor publishes the permission decision.

The planner's digest renderer used to reconstruct whether a tool was
prompted / auto-approved / denied by re-running
``executor.profile.auto_approve()`` and string-matching the executor's
``"tool not approved by user"`` summary. The executor already made
the decision once and threw it away — duplicating it in the planner
makes the two paths drift over time.

This file pins the new seam:

* ``AgentExecutor.execute()`` attaches ``permission`` to every result
  dict with ``{"prompted": bool, "approved": bool}``.
* The denial summary is a module-level constant ``DENIED_SUMMARY`` so
  the magic string lives in one place.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.executor import DENIED_SUMMARY, AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class ExecutorPublishesPermissionTests(unittest.TestCase):
    def test_dry_run_marks_result_as_auto_approved(self) -> None:
        # Dry-run skips the permission machinery entirely; the
        # permission record must still be populated so the planner
        # does not have to special-case the dry-run path.
        registry = AgentToolRegistry()
        with TemporaryDirectory() as tmp:
            executor = AgentExecutor(
                registry,
                session_dir=Path(tmp),
                trace_path=Path(tmp) / "trace.jsonl",
                dry_run=True,
                profile=Profile.SAFE,
            )
            result = executor.execute(ToolCall(name="list_skills", args={}))
        self.assertIn("permission", result)
        self.assertEqual(
            result["permission"],
            {"prompted": False, "approved": True},
        )

    def test_quick_profile_marks_read_only_as_auto_approved(self) -> None:
        # QUICK profile auto-approves read-only tools without
        # prompting; the permission record must reflect that.
        registry = AgentToolRegistry()
        with TemporaryDirectory() as tmp:
            executor = AgentExecutor(
                registry,
                session_dir=Path(tmp),
                trace_path=Path(tmp) / "trace.jsonl",
                dry_run=False,
                profile=Profile.QUICK,
            )
            result = executor.execute(ToolCall(name="list_skills", args={}))
        self.assertEqual(
            result["permission"],
            {"prompted": False, "approved": True},
        )
        self.assertTrue(result["ok"])

    def test_denial_summary_constant_matches_legacy_string(self) -> None:
        # PR #192 hard-coded "tool not approved by user" in two
        # places; hoisting to a module-level constant lets the planner
        # import it instead of magic-stringing the same literal.
        self.assertEqual(DENIED_SUMMARY, "tool not approved by user")

    def test_denied_prompt_records_prompted_and_denied(self) -> None:
        # SAFE profile + a write tool + a permissions.prompt_user that
        # returns False exercises the denial branch end-to-end. The
        # permission record must say prompted=True, approved=False so
        # the planner can render the (skipped) tail without sniffing
        # the summary.
        registry = AgentToolRegistry()
        with TemporaryDirectory() as tmp:
            executor = AgentExecutor(
                registry,
                session_dir=Path(tmp),
                trace_path=Path(tmp) / "trace.jsonl",
                dry_run=False,
                profile=Profile.SAFE,
            )
            # Steer permissions.prompt_user toward denial. ``write_file``
            # is registered as a write tool that SAFE always prompts on.
            with mock.patch(
                "agentic_swmm.agent.permissions.prompt_user",
                return_value=False,
            ):
                result = executor.execute(
                    ToolCall(
                        name="write_file",
                        args={"path": "x.txt", "content": "y"},
                    )
                )
        self.assertEqual(
            result["permission"],
            {"prompted": True, "approved": False},
        )
        self.assertFalse(result["ok"])
        # The legacy summary string is still emitted — callers that
        # already log it untouched continue to work.
        self.assertEqual(result["summary"], DENIED_SUMMARY)


if __name__ == "__main__":
    unittest.main()
