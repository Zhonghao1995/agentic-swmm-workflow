"""Regression: a plot-continuation prompt yields a 2-call plan.

PRD_runtime Done Criteria:
  ``test_plot_continuation_dedup`` passes with plan length == 2.

Setup mirrors the user's session: an ``active_run_dir`` exists with a
prior turn's tool history (the 10-call pathology fixture). The Chinese
prompt ``换成 J2 depth plot`` must short-circuit straight to
``inspect_plot_options`` + ``plot_run`` — no introspection, no
``select_workflow_mode``.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall


class _CannedExecutor:
    """Fake executor returning canned ``inspect_plot_options`` /
    ``plot_run`` results so the regression doesn't hit the real SWMM
    pipeline.
    """

    def __init__(self, session_dir: Path) -> None:
        self.session_dir = session_dir
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        if call.name == "inspect_plot_options":
            result = {
                "tool": call.name,
                "args": call.args,
                "ok": True,
                "summary": "rain=1 nodes=3 attrs=4",
                "results": {
                    "rainfall_options": [{"name": "MACAO_94_23", "used_by_raingage": True}],
                    "node_options": ["J1", "J2", "O1"],
                    "node_attribute_options": [
                        {"name": "Depth_above_invert", "label": "depth above invert"},
                        {"name": "Total_inflow", "label": "total inflow"},
                    ],
                    "defaults": {
                        "rain_ts": "MACAO_94_23",
                        "node": "J1",
                        "node_attr": "Total_inflow",
                    },
                    "selections_needed": [],
                    "user_prompt": "",
                },
            }
        elif call.name == "plot_run":
            result = {
                "tool": call.name,
                "args": call.args,
                "ok": True,
                "summary": "plot saved",
            }
        else:  # pragma: no cover - sentinel for unexpected planner calls
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "aiswmm_state_10call_pathology.json"
)


class PlotContinuationDedupTests(unittest.TestCase):
    def test_chinese_plot_continuation_yields_two_calls(self) -> None:
        prior_state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        # Sanity-check the fixture shape we depend on.
        self.assertIn("active_run_dir", prior_state)
        self.assertGreaterEqual(len(prior_state.get("tool_history", [])), 7)

        planner = OpenAIPlanner(
            provider=None,  # type: ignore[arg-type]
            registry=AgentToolRegistry(),
            max_steps=8,
            verbose=False,
            emit=lambda text: None,
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            executor = _CannedExecutor(session_dir)
            outcome = planner.run(
                goal="换成 J2 depth plot",
                session_dir=session_dir,
                trace_path=session_dir / "agent_trace.jsonl",
                executor=executor,
                prior_session_state=prior_state,
            )

        self.assertTrue(outcome.ok)
        # Plan length is the heart of the regression: exactly two calls.
        plan_names = [call.name for call in outcome.plan]
        self.assertEqual(
            plan_names,
            ["inspect_plot_options", "plot_run"],
            f"plot continuation plan should be [inspect_plot_options, plot_run]; got {plan_names}",
        )
        self.assertEqual(
            len(outcome.plan),
            2,
            f"plot continuation plan length must be 2; got {len(outcome.plan)}",
        )


if __name__ == "__main__":
    unittest.main()
