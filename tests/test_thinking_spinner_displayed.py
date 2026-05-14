"""Thinking spinner runs during the LLM call (issue #58, UX-3).

Currently ``OpenAIPlanner.run`` calls ``provider.respond_with_tools``
synchronously and produces no output during the 5-30s wait.  This test
pins the contract: when the provider call takes time, the spinner
emits ``Thinking…`` with carriage-return framing, and the line is
cleared (newline-terminated) once the provider returns.

The fake provider sleeps briefly and returns a no-tool-calls response
so the planner exits after the first step.
"""
from __future__ import annotations

import io
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolResponse


class _FakeTTYStream(io.StringIO):
    def isatty(self) -> bool:  # type: ignore[override]
        return True


class _SleepingProvider:
    """Fake provider whose ``respond_with_tools`` blocks for ~1s."""

    model = "fake-model"

    def __init__(self, sleep_seconds: float = 1.0) -> None:
        self._sleep_seconds = sleep_seconds
        self.calls = 0

    def respond_with_tools(self, **_kwargs: Any) -> ProviderToolResponse:
        self.calls += 1
        time.sleep(self._sleep_seconds)
        # No tool_calls → planner returns after one step.
        return ProviderToolResponse(
            text="done",
            model=self.model,
            response_id="resp-1",
            tool_calls=[],
            raw={},
        )


class ThinkingSpinnerDisplayedTests(unittest.TestCase):
    def test_thinking_spinner_renders_during_llm_call(self) -> None:
        provider = _SleepingProvider(sleep_seconds=1.0)
        registry = AgentToolRegistry()
        stream = _FakeTTYStream()
        snapshots: list[str] = []
        done = threading.Event()

        def poll_stream() -> None:
            # Capture mid-flight output every ~100 ms while the
            # provider is still sleeping. We expect the spinner to be
            # rendered (carriage-return framed) before the call
            # returns.
            for _ in range(15):
                if done.is_set():
                    return
                snapshots.append(stream.getvalue())
                time.sleep(0.1)

        poller = threading.Thread(target=poll_stream)
        poller.start()
        try:
            with TemporaryDirectory() as tmp:
                session_dir = Path(tmp)
                trace_path = session_dir / "trace.jsonl"
                executor = AgentExecutor(
                    registry,
                    session_dir=session_dir,
                    trace_path=trace_path,
                    dry_run=True,
                    profile=Profile.QUICK,
                )
                planner = OpenAIPlanner(
                    provider,  # type: ignore[arg-type]
                    registry,
                    max_steps=1,
                    progress_stream=stream,
                )
                planner.run(
                    goal="hello",
                    session_dir=session_dir,
                    trace_path=trace_path,
                    executor=executor,
                )
        finally:
            done.set()
            poller.join(timeout=2.0)

        final_output = stream.getvalue()
        # 1) Thinking… was emitted at some point.
        self.assertIn(
            "Thinking",
            final_output,
            f"final output must contain 'Thinking'; got {final_output!r}",
        )
        # 2) Carriage-return framing was used (TTY path).
        self.assertIn(
            "\r",
            final_output,
            "thinking spinner must use carriage-return framing on TTY",
        )
        # 3) Spinner rendered MID-FLIGHT — at least one snapshot taken
        # before the provider returned contains "Thinking".
        mid_flight_hit = any("Thinking" in snap for snap in snapshots[:8])
        self.assertTrue(
            mid_flight_hit,
            f"spinner must render before provider returns; snapshots={snapshots[:8]!r}",
        )
        # 4) Line cleared after the response — last char of final
        # output is a newline so the next line of agent output starts
        # cleanly.
        self.assertTrue(
            final_output.endswith("\n"),
            f"thinking spinner must terminate the line on finish; got {final_output[-10:]!r}",
        )


if __name__ == "__main__":
    unittest.main()
