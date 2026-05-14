"""Regression: planner dedupes introspection across turns.

PRD_runtime Done Criteria:
  ``test_session_introspection_dedup`` passes — ``list_skills`` and
  ``list_mcp_servers`` each called <= 1 time across 3 turns.

We drive the planner's introspection block directly using a fake
executor that records every ``ToolCall``. The first turn runs against
empty state (full introspection happens); turns 2 and 3 see the prior
turn's tool history and must skip.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class _RecordingExecutor:
    """Minimal executor that records calls and returns canned results."""

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


def _names(executor: _RecordingExecutor) -> list[str]:
    return [call.name for call in executor.recorded]


class IntrospectionDedupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AgentToolRegistry()
        # The planner constructor needs a provider; we never call run(),
        # only the private _consult_workflow_skills helper, so a None
        # provider is acceptable here.
        self.planner = OpenAIPlanner(
            provider=None,  # type: ignore[arg-type]
            registry=self.registry,
            max_steps=8,
            verbose=False,
            emit=lambda text: None,
        )

    def test_three_turns_each_call_list_skills_at_most_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            executor = _RecordingExecutor(session_dir)

            # Turn 1: no prior state — full introspection runs.
            plan1: list[ToolCall] = []
            self.planner._consult_workflow_skills(
                goal="run examples/tecnopolo.inp",
                plan=plan1,
                executor=executor,
                prior_session_state=None,
            )
            self.assertIn("list_skills", _names(executor))
            self.assertIn("list_mcp_servers", _names(executor))

            # Build a prior_state shape that mirrors what runtime_loop
            # will load from disk on the next turn.
            prior_state = {"tool_history": [{"tool": c.name, "args": c.args} for c in plan1]}

            # Turn 2: prior introspection visible.
            plan2: list[ToolCall] = []
            self.planner._consult_workflow_skills(
                goal="run examples/tecnopolo.inp again",
                plan=plan2,
                executor=executor,
                prior_session_state=prior_state,
            )

            # Turn 3: still skipping.
            prior_state["tool_history"].extend({"tool": c.name, "args": c.args} for c in plan2)
            plan3: list[ToolCall] = []
            self.planner._consult_workflow_skills(
                goal="plot J2 inflow",
                plan=plan3,
                executor=executor,
                prior_session_state=prior_state,
            )

            calls = _names(executor)
            self.assertLessEqual(
                calls.count("list_skills"),
                1,
                f"list_skills called {calls.count('list_skills')} times across 3 turns",
            )
            self.assertLessEqual(
                calls.count("list_mcp_servers"),
                1,
                f"list_mcp_servers called {calls.count('list_mcp_servers')} times across 3 turns",
            )


if __name__ == "__main__":
    unittest.main()
