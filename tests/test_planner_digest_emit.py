"""PRD-185: planner emits digest single lines by default.

The OpenAI planner used to emit two lines per tool step:

    [1] select_skill
    OK: selected skill swmm-agent-internal: 4 tool(s) (registry)

Plus the executor's spinner re-printed the tool's description. The
PRD's digest mode collapses all that to one line per step.

This test pins the per-step contract directly on the planner: the
captured ``emit`` callable receives ONE rendered string per step
when ``verbose=False`` (the new default) and TWO when ``verbose=True``
(legacy debugging path). Spinner suppression and the final summary
block are covered in their own test files.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _ScriptedProvider:
    """Yields one tool_call response then a final text-only response."""

    model = "test-model"

    def __init__(self, tool_calls: list[ProviderToolCall]) -> None:
        self._batches: list[list[ProviderToolCall]] = [tool_calls, []]
        self._step = 0

    def respond_with_tools(self, **_kwargs: Any) -> ProviderToolResponse:
        batch = self._batches[min(self._step, len(self._batches) - 1)]
        self._step += 1
        return ProviderToolResponse(
            text="done" if not batch else "",
            model=self.model,
            response_id=f"resp-{self._step}",
            tool_calls=batch,
            raw={},
        )


def _make_planner(emit, verbose: bool) -> tuple[OpenAIPlanner, AgentToolRegistry]:
    registry = AgentToolRegistry()
    provider = _ScriptedProvider(
        [
            ProviderToolCall(call_id="c1", name="list_skills", arguments={}),
        ]
    )
    planner = OpenAIPlanner(
        provider,  # type: ignore[arg-type]
        registry,
        max_steps=2,
        verbose=verbose,
        emit=emit,
    )
    return planner, registry


class DigestEmitsOneLinePerStepTests(unittest.TestCase):
    def test_verbose_false_emits_single_digest_line(self) -> None:
        emitted: list[str] = []
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            planner, registry = _make_planner(emitted.append, verbose=False)
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=trace_path,
                dry_run=False,
                profile=Profile.QUICK,
            )
            planner.run(
                # Use a non-SWMM goal so the planner skips the
                # workflow-mode short-circuit and goes straight into
                # the main LLM loop where our step lives.
                goal="just an introspection call please",
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,
            )
        # Filter out any non-step lines (warm-up / summary text).
        step_lines = [line for line in emitted if line.startswith("[1]")]
        self.assertEqual(
            len(step_lines),
            1,
            f"digest mode must emit exactly ONE line for step 1, got: {step_lines!r}",
        )
        # The single line carries the (read-only, auto) tag and the ✓ marker.
        self.assertIn("(read-only, auto)", step_lines[0])
        self.assertIn("✓", step_lines[0])
        # And the standalone "OK: ..." legacy line must NOT appear.
        self.assertFalse(
            any(line.startswith("OK:") for line in emitted),
            f"digest mode must NOT emit standalone OK: lines, got: {emitted!r}",
        )

    def test_verbose_true_keeps_legacy_two_line_emit(self) -> None:
        emitted: list[str] = []
        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            planner, registry = _make_planner(emitted.append, verbose=True)
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=trace_path,
                dry_run=False,
                profile=Profile.QUICK,
            )
            planner.run(
                goal="just an introspection call please",
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,
            )
        # Verbose keeps the two-line shape unchanged: ``[N] tool {args}``
        # then ``OK|FAILED: <summary>``.
        step_header_lines = [
            line for line in emitted if line.startswith("[1] list_skills")
        ]
        ok_lines = [line for line in emitted if line.startswith("OK:")]
        self.assertEqual(
            len(step_header_lines),
            1,
            f"verbose must keep the legacy header line, got: {emitted!r}",
        )
        self.assertEqual(
            len(ok_lines),
            1,
            f"verbose must keep the legacy OK: line, got: {emitted!r}",
        )
        # The verbose header line carries the args JSON.
        self.assertIn("{}", step_header_lines[0])


if __name__ == "__main__":
    unittest.main()
