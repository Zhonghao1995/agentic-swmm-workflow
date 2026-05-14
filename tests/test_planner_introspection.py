"""Unit tests for ``PlannerIntrospection.should_introspect``.

PRD_runtime "Module: PlannerIntrospection":
- ``should_introspect(session_state, prompt) -> tuple[skip_skills, skip_mcp]``
- ``skip_skills = True`` when state already contains a ``list_skills`` call.
- ``skip_mcp = True`` when state already contains ``list_mcp_servers`` and
  at least one ``list_mcp_tools`` for each relevant server.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.planner_introspection import should_introspect


class ShouldIntrospectTests(unittest.TestCase):
    def test_empty_state_returns_no_skips(self) -> None:
        # Case 1: completely empty state, fresh session.
        skip_skills, skip_mcp = should_introspect({}, "list skills")
        self.assertFalse(skip_skills)
        self.assertFalse(skip_mcp)

    def test_fresh_session_with_only_user_prompt_returns_no_skips(self) -> None:
        # Case 2: state has no plan / tool_history at all.
        state = {"goal": "hello", "case_id": "x"}
        skip_skills, skip_mcp = should_introspect(state, "what can you do")
        self.assertFalse(skip_skills)
        self.assertFalse(skip_mcp)

    def test_active_run_dir_alone_does_not_imply_skip(self) -> None:
        # Case 3: workflow state has an active run dir, but planner has
        # not yet inspected skills or MCP. Do not skip — there is no
        # evidence of prior introspection.
        state = {
            "workflow_state": {"active_run_dir": "runs/agent/interactive/session/runs/003"},
        }
        skip_skills, skip_mcp = should_introspect(state, "plot J2 depth")
        self.assertFalse(skip_skills)
        self.assertFalse(skip_mcp)

    def test_list_skills_in_tool_history_skips_skills(self) -> None:
        # Case 4: tool_history already contains list_skills — skip on
        # this turn. MCP introspection still missing.
        state = {
            "tool_history": [
                {"tool": "list_skills", "args": {}},
            ],
        }
        skip_skills, skip_mcp = should_introspect(state, "next turn")
        self.assertTrue(skip_skills)
        self.assertFalse(skip_mcp)

    def test_full_introspection_in_history_skips_both(self) -> None:
        # Case 5: the 10-call pathology — list_skills, three read_skill
        # calls, list_mcp_servers, two list_mcp_tools (one per relevant
        # server). Subsequent turns should skip both.
        state = {
            "tool_history": [
                {"tool": "list_skills", "args": {}},
                {"tool": "read_skill", "args": {"skill_name": "swmm-end-to-end"}},
                {"tool": "read_skill", "args": {"skill_name": "swmm-runner"}},
                {"tool": "read_skill", "args": {"skill_name": "swmm-plot"}},
                {"tool": "list_mcp_servers", "args": {}},
                {"tool": "list_mcp_tools", "args": {"server": "swmm-runner"}},
                {"tool": "list_mcp_tools", "args": {"server": "swmm-plot"}},
            ],
        }
        skip_skills, skip_mcp = should_introspect(state, "换成 J2 depth plot")
        self.assertTrue(skip_skills)
        self.assertTrue(skip_mcp)

    def test_plan_field_is_equivalent_to_tool_history(self) -> None:
        # PRD writes ``session_state["plan"]`` but the on-disk schema
        # also uses ``tool_history``. Accept either shape.
        state = {
            "plan": [
                {"tool": "list_skills", "args": {}},
                {"tool": "list_mcp_servers", "args": {}},
                {"tool": "list_mcp_tools", "args": {"server": "swmm-runner"}},
            ],
        }
        skip_skills, skip_mcp = should_introspect(state, "next")
        self.assertTrue(skip_skills)
        self.assertTrue(skip_mcp)

    def test_list_mcp_servers_alone_does_not_skip_mcp(self) -> None:
        # mcp servers without any list_mcp_tools — still need at least
        # one list_mcp_tools to count as fully introspected.
        state = {
            "tool_history": [
                {"tool": "list_mcp_servers", "args": {}},
            ],
        }
        _, skip_mcp = should_introspect(state, "next")
        self.assertFalse(skip_mcp)


class TenCallPathologyFixtureTests(unittest.TestCase):
    """End-to-end check using the committed pathology fixture."""

    def test_pathology_state_results_in_both_skips(self) -> None:
        import json
        from pathlib import Path

        fixture = (
            Path(__file__).resolve().parent / "fixtures" / "aiswmm_state_10call_pathology.json"
        )
        state = json.loads(fixture.read_text(encoding="utf-8"))
        skip_skills, skip_mcp = should_introspect(state, "换成 J2 的水深图")
        self.assertTrue(skip_skills, "list_skills already in tool_history")
        self.assertTrue(skip_mcp, "list_mcp_servers and list_mcp_tools in tool_history")


if __name__ == "__main__":
    unittest.main()
