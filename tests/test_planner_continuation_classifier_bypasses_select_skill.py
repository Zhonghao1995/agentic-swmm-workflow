"""Continuation classifier bypasses ``select_skill`` (PRD-Y).

PRD-Y "Continuation classifier (PRD_runtime) compatibility": when the
classifier returns ``PLOT_CONTINUATION`` because there is an
``active_run_dir`` in the prior state AND the prompt matches the
plot-continuation heuristic, the planner skips ``select_skill`` and
goes straight to ``inspect_plot_options`` + ``plot_run``. PRD_runtime's
2-call dedup gate still holds — the new skill-routing surface must not
add extra hops to this path.
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
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "plot saved"}
        else:  # pragma: no cover - sentinel
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "aiswmm_state_10call_pathology.json"
)


class ContinuationClassifierBypassTests(unittest.TestCase):
    def test_plot_continuation_skips_select_skill_and_yields_two_calls(self) -> None:
        prior_state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.assertIn("active_run_dir", prior_state)

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
                executor=executor,  # type: ignore[arg-type]
                prior_session_state=prior_state,
            )

        plan_names = [call.name for call in outcome.plan]
        self.assertTrue(outcome.ok)
        # PRD-Y must preserve PRD_runtime's 2-call gate: no extra
        # select_skill hop and no extra introspection in this case.
        self.assertNotIn(
            "select_skill",
            plan_names,
            f"continuation classifier must skip select_skill; plan was {plan_names}",
        )
        self.assertEqual(
            plan_names,
            ["inspect_plot_options", "plot_run"],
            f"expected 2-call plot continuation; got {plan_names}",
        )


if __name__ == "__main__":
    unittest.main()
