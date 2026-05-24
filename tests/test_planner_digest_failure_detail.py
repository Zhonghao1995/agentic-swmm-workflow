"""Issue #193 item 1: failure-detail source-fallback chain in `_emit_step`.

PRD-185 promised "the user never has to re-run with ``--verbose`` to
see why a tool failed". The original digest-mode wiring only checked
``result['stderr_tail']`` / ``result['stdout_tail']`` when populating
the auto-expanded ``Detail:`` block.

These tests pin the priority order used by ``_emit_step``:

    stderr_tail > stdout_tail > error > message > traceback

``stderr_tail`` / ``stdout_tail`` are populated today by every
subprocess-shaped handler. ``error`` / ``message`` / ``traceback`` are
*reserved* slots — no production handler emits them at the moment, but
the priority order is pinned here so the digest UX is forward-compatible
as handlers adopt those keys later.

The first non-empty key wins. Tests drive the planner the same way
``tests/test_planner_digest_emit.py`` does so the contract is
verified end-to-end through the production code path.
"""
from __future__ import annotations

import unittest
from typing import Any

from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class _StubExecutor:
    """Lightweight stand-in for ``AgentExecutor`` that returns a canned
    failure result on each ``execute()`` call.

    Used so the tests can exercise ``_emit_step`` directly without
    setting up a real registry / handler. ``profile`` mirrors the
    interface the planner reads to reconstruct the permission decision.
    """

    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.dry_run = False
        # Read-only tool path => auto-approved in QUICK profile.
        self.profile = Profile.QUICK

    def execute(self, call: ToolCall, *, index: int | None = None) -> dict[str, Any]:
        return dict(self._result)


def _emit_failure(*, result: dict[str, Any], emitted: list[str]) -> None:
    """Run ``_emit_step`` once for a synthetic failed ``list_dir`` call."""
    registry = AgentToolRegistry()
    planner = OpenAIPlanner(
        provider=None,  # type: ignore[arg-type]
        registry=registry,
        max_steps=2,
        verbose=False,
        emit=emitted.append,
    )
    call = ToolCall(name="list_dir", args={"path": "x"})
    executor = _StubExecutor(result)
    planner._emit_step(index=1, call=call, result=result, executor=executor)


class FailureDetailSourceFallbackTests(unittest.TestCase):
    def test_stderr_tail_still_wins_when_present(self) -> None:
        # Regression guard: the new fallback must not displace the
        # existing high-priority stderr_tail / stdout_tail sources.
        emitted: list[str] = []
        _emit_failure(
            result={
                "tool": "list_dir",
                "ok": False,
                "summary": "boom",
                "stderr_tail": "ENOENT on /missing/path\n",
                "error": "should not appear",
            },
            emitted=emitted,
        )
        block = "\n".join(emitted)
        self.assertIn("Detail: ENOENT on /missing/path", block)
        self.assertNotIn("should not appear", block)

    def test_error_key_populates_detail_when_no_tails(self) -> None:
        # The bug from issue #193 item 1: handlers like
        # ``uncertainty_plan.py`` carry detail in ``error``. Without
        # this fallback the digest line is "✗ boom" with no Detail.
        emitted: list[str] = []
        _emit_failure(
            result={
                "tool": "list_dir",
                "ok": False,
                "summary": "boom",
                "error": "RuntimeError: planner aborted",
            },
            emitted=emitted,
        )
        block = "\n".join(emitted)
        self.assertIn("Detail: RuntimeError: planner aborted", block)

    def test_message_key_populates_detail_when_no_tails_or_error(self) -> None:
        # ``message`` is the gap-decision shape's failure note.
        emitted: list[str] = []
        _emit_failure(
            result={
                "tool": "list_dir",
                "ok": False,
                "summary": "evidence missing",
                "message": "no evidence_ref attached to candidate",
            },
            emitted=emitted,
        )
        block = "\n".join(emitted)
        self.assertIn(
            "Detail: no evidence_ref attached to candidate",
            block,
        )

    def test_traceback_key_populates_detail_when_only_traceback(self) -> None:
        # Multi-line tracebacks should land in the Detail block too;
        # ``render_step`` already handles continuation indenting.
        tb = (
            "Traceback (most recent call last):\n"
            "  File 'x.py', line 1\n"
            "    raise X"
        )
        emitted: list[str] = []
        _emit_failure(
            result={
                "tool": "list_dir",
                "ok": False,
                "summary": "blew up",
                "traceback": tb,
            },
            emitted=emitted,
        )
        block = "\n".join(emitted)
        self.assertIn("Detail: Traceback (most recent call last):", block)
        # Continuation lines stay indented under the Detail block.
        self.assertIn("    raise X", block)


if __name__ == "__main__":
    unittest.main()
