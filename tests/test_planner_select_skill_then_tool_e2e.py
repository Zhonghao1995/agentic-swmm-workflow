"""End-to-end: planner → select_skill → deterministic-SWMM tool → MCP.

PRD-Y user-story 9 + Testing Decisions: "single comprehensive E2E test
that exercises 'skill choose → tool call → MCP server → Python script
→ result returned' against the live MCP server."

We use a stub provider that emits the exact two-call sequence we want
(``select_skill("swmm-builder")`` then ``build_inp(...)``) and a REAL
``MCPPool`` bound to the user's runtime MCP registry. Build_inp wraps
the swmm-builder MCP server, which spawns ``python3 build_swmm_inp.py``
internally — when the inputs are bad the Python script reports the
error and the MCP server returns that as a tool result. PRD-Y's
fail-soft contract means the planner's loop still completes cleanly
and writes a ``skill_selected`` trace event regardless of whether the
underlying script succeeds.

Skips when ``node`` is missing or ``mcp/swmm-builder/node_modules`` is
not installed — those environments cannot run the real MCP server.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest

from agentic_swmm.agent import mcp_pool
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.mcp_pool import MCPPool, ServerSpec, bind_session_pool, clear_session_pool
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILDER_SERVER_JS = REPO_ROOT / "mcp" / "swmm-builder" / "server.js"
NETWORK_SERVER_JS = REPO_ROOT / "mcp" / "swmm-network" / "server.js"
NETWORK_EXAMPLE = REPO_ROOT / "skills" / "swmm-network" / "examples" / "basic-network.json"


def _require_node_environment() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH; skipping e2e select_skill test")
    for server_js, name in [
        (BUILDER_SERVER_JS, "swmm-builder"),
        (NETWORK_SERVER_JS, "swmm-network"),
    ]:
        if not server_js.exists():
            pytest.skip(f"missing MCP server file: {server_js}")
        if not (server_js.parent / "node_modules").exists():
            pytest.skip(
                f"mcp/{name}/node_modules is missing; run "
                "scripts/install_mcp_deps.sh (or aiswmm setup --install-mcp)"
            )


class _ScriptedProvider:
    def __init__(self, responses: list[ProviderToolResponse]) -> None:
        self._responses = list(responses)
        self.calls_received: list[list[dict[str, Any]]] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self.calls_received.append(list(input_items))
        if not self._responses:
            raise AssertionError("scripted provider exhausted")
        return self._responses.pop(0)


def _tool_call(name: str, args: dict[str, Any], *, call_id: str) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id, name=name, arguments=args)


def _tool_response(calls: list[ProviderToolCall], *, response_id: str) -> ProviderToolResponse:
    return ProviderToolResponse(text="", model="stub", response_id=response_id, tool_calls=calls, raw={})


def _final(text: str) -> ProviderToolResponse:
    return ProviderToolResponse(text=text, model="stub", response_id="final", tool_calls=[], raw={})


def _bind_real_builder_pool() -> MCPPool:
    pool = MCPPool(
        [
            ServerSpec(name="swmm-builder", command="node", args=[str(BUILDER_SERVER_JS)]),
            ServerSpec(name="swmm-network", command="node", args=[str(NETWORK_SERVER_JS)]),
        ]
    )
    bind_session_pool(pool)
    return pool


class PlannerSelectSkillThenToolE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        _require_node_environment()
        clear_session_pool()

    def tearDown(self) -> None:
        pool = mcp_pool.session_pool()
        if pool is not None:
            try:
                pool.shutdown()
            finally:
                clear_session_pool()

    def test_planner_select_skill_then_network_qa_against_real_mcp(self) -> None:
        """End-to-end: select_skill('swmm-network') → network_qa.

        We use ``network_qa`` (not ``build_inp``) for the live MCP call
        because the swmm-network ``qa`` tool has a single-argument schema
        — ``networkJsonPath`` — and the repo ships a known-good fixture.
        The full ``build_inp`` flow needs five concurrent input
        artefacts (params + climate + network + …) that the repo does
        not ship as a single set, so verifying that bigger chain end to
        end is out of scope for this regression. The handler wire is
        identical though: a ``ToolSpec`` whose handler is built via
        ``_make_mcp_routed_handler`` invokes the pool, which spawns
        ``node mcp/swmm-network/server.js`` and gets back a real reply
        produced by the Python script.
        """

        self.assertTrue(NETWORK_EXAMPLE.is_file(), f"missing fixture: {NETWORK_EXAMPLE}")

        provider = _ScriptedProvider(
            [
                # Round 1: pick the skill.
                _tool_response(
                    [_tool_call("select_skill", {"skill_name": "swmm-network"}, call_id="c1")],
                    response_id="r1",
                ),
                # Round 2: invoke the concrete tool. Path is repo-relative.
                _tool_response(
                    [
                        _tool_call(
                            "network_qa",
                            {"network_json": str(NETWORK_EXAMPLE.relative_to(REPO_ROOT))},
                            call_id="c2",
                        )
                    ],
                    response_id="r2",
                ),
                _final("done"),
            ]
        )

        registry = AgentToolRegistry()
        _bind_real_builder_pool()

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            executor = AgentExecutor(
                registry,
                session_dir=session_dir,
                trace_path=trace_path,
                dry_run=False,
                profile=Profile.QUICK,
            )
            planner = OpenAIPlanner(
                provider=provider,  # type: ignore[arg-type]
                registry=registry,
                max_steps=4,
                verbose=False,
                emit=lambda text: None,
            )
            # Goal that does NOT look like a SWMM request — keeps the
            # planner in the OpenAI agent loop so our scripted call
            # sequence runs.
            outcome = planner.run(
                goal="tell me about this repository",
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,
            )

            self.assertTrue(outcome.ok, f"planner failed: {outcome.final_text!r}")

            events = []
            with trace_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    events.append(json.loads(line))

        # ``skill_selected`` event was written between session_start
        # and the first tool_result, per PRD-Y.
        skill_events = [e for e in events if e.get("event") == "skill_selected"]
        self.assertEqual(len(skill_events), 1)
        self.assertEqual(skill_events[0].get("skill_name"), "swmm-network")

        # The network_qa tool actually fired against the real MCP
        # server: tool_result event must be present and ok=True (the
        # fixture is valid).
        tool_results = [e for e in events if e.get("event") == "tool_result" and e.get("tool") == "network_qa"]
        self.assertEqual(len(tool_results), 1)
        self.assertTrue(tool_results[0].get("ok"), f"network_qa failed: {tool_results[0]}")
        # The MCP server's text content (the JSON output from
        # ``network_qa.py``) round-trips through ``_wrap_mcp_result`` as
        # an excerpt. We expect to see the validation summary fields.
        excerpt = str(tool_results[0].get("excerpt") or "")
        self.assertIn("junction_count", excerpt)


if __name__ == "__main__":
    unittest.main()
