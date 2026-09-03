"""An outward-facing tool asks again for a new area within a turn.

Live test 2026-09-03 (S45, two cities in one session): under the quick
profile the first fetch asked for Victoria and the second fetch, for
Regina, ran under that approval. For a tool whose argument is the area
that leaves the machine, a new area is a new consent.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from agentic_swmm.agent import repl as repl_mod
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.intent_classifier import classify_intent
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.session_bootstrap import infer_case_slug
from agentic_swmm.agent.types import ToolCall, ToolSpec


class _TinyRegistry:
    """Two consequential tools + one read-only, no side effects."""

    def __init__(self) -> None:
        self.specs = {
            "fetch": ToolSpec("fetch", "fetch", {"type": "object"}, lambda c, s: {"ok": True, "summary": "fetched"}),
            "run": ToolSpec("run", "run", {"type": "object"}, lambda c, s: {"ok": True, "summary": "ran"}),
        }

    def is_read_only(self, name: str) -> bool:
        return False

    def execute(self, call: ToolCall, session_dir: Path):
        return dict(self.specs[call.name].handler(call, session_dir))


def _tiny_with(names: tuple[str, ...]):
    """A registry whose spec names are the outward tool and an inward one."""
    reg = _TinyRegistry()
    specs = {}
    for name in names:
        template = next(iter(reg.specs.values()))
        specs[name] = type(template)(*[getattr(template, f) for f in template.__dataclass_fields__]) if hasattr(template, "__dataclass_fields__") else template
        try:
            specs[name].name = name
        except Exception:
            pass
    reg.specs = specs
    return reg


class OutwardToolsAskAgainTests(unittest.TestCase):
    def _executor(self, tmp: Path) -> AgentExecutor:
        return AgentExecutor(
            _tiny_with(("fetch_swmm_from_canada", "run_swmm_inp")),
            session_dir=tmp,
            trace_path=tmp / "trace.jsonl",
            dry_run=False,
            profile=Profile.QUICK,
        )

    def test_a_second_city_prompts_again_and_a_repeat_does_not(self) -> None:
        with TemporaryDirectory() as tmp:
            ex = self._executor(Path(tmp))
            with mock.patch("agentic_swmm.agent.executor.permissions.request_approval") as approval:
                approval.return_value = mock.Mock(approved=True, needs_guidance=False)
                r1 = ex.execute(ToolCall(name="fetch_swmm_from_canada", args={"city": "Victoria", "start_date": "2023-11-01", "end_date": "2023-11-04"}))
                r2 = ex.execute(ToolCall(name="fetch_swmm_from_canada", args={"city": "Regina", "start_date": "2023-11-01", "end_date": "2023-11-04"}))
                r3 = ex.execute(ToolCall(name="fetch_swmm_from_canada", args={"city": "Regina", "start_date": "2023-11-01", "end_date": "2023-11-04"}))
                r4 = ex.execute(ToolCall(name="run_swmm_inp", args={"inp_path": "x.inp"}))
            self.assertEqual(approval.call_count, 2)
            self.assertTrue(r1["permission"]["prompted"])
            self.assertTrue(r2["permission"]["prompted"])
            self.assertFalse(r3["permission"]["prompted"])
            self.assertFalse(r4["permission"]["prompted"])
            self.assertEqual(approval.call_args_list[1].args[1], "city=Regina, 2023-11-01..2023-11-04")
