"""The MCP tools listing budgets ten seconds by default.

Live test 2026-09-03 (S48 r3, S53): the introspection prologue asked for
3-second listings; while SWMM sweeps kept the machine busy, a Node server
needed longer to start and the listing failed with "MCP process ended
before sending a complete line", costing a call and a confusing digest line.
An explicit timeout_seconds is still honored (the CLI test pins that).
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from agentic_swmm.agent import tool_registry
from agentic_swmm.agent.types import ToolCall

REPO_ROOT = Path(__file__).resolve().parents[1]


def _listing_timeout(args: dict, tmp_path: Path) -> int:
    seen: dict[str, int] = {}

    def fake_list_tools(command, cmd_args, *, timeout):
        seen["timeout"] = timeout
        return [{"name": "swmm_run", "description": "run"}]

    server = {"name": "swmm-runner", "command": "node", "args": ["x.js"]}
    with mock.patch.object(tool_registry, "_mcp_server", return_value=server), mock.patch.object(
        tool_registry.mcp_cache, "read_cached_tools", return_value=None
    ), mock.patch.object(tool_registry.mcp_cache, "write_cached_tools", lambda *a, **k: None), mock.patch.object(
        tool_registry.mcp_client, "list_tools", side_effect=fake_list_tools
    ):
        tool_registry._list_mcp_tools_tool(ToolCall(name="list_mcp_tools", args={"server": "swmm-runner", "refresh": True, **args}), tmp_path)
    return seen["timeout"]


def test_an_unspecified_timeout_gets_ten_seconds(tmp_path: Path) -> None:
    assert _listing_timeout({}, tmp_path) == tool_registry.MCP_LISTING_DEFAULT_TIMEOUT == 10


def test_an_explicit_timeout_is_still_honored(tmp_path: Path) -> None:
    assert _listing_timeout({"timeout_seconds": 4}, tmp_path) == 4


def test_the_prologue_asks_for_ten_seconds() -> None:
    source = (REPO_ROOT / "agentic_swmm" / "agent" / "planner.py").read_text(encoding="utf-8")
    assert 'ToolCall("list_mcp_tools", {"server": name, "timeout_seconds": 10})' in source
    assert 'ToolCall("list_mcp_tools", {"server": name, "timeout_seconds": 3})' not in source
