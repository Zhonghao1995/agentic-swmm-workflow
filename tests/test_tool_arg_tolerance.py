"""Two planner habits the tools now tolerate (S04 turn 4, 2026-09-02).

The planner quoted ``agent_trace.jsonl:391`` (grep style) and was told the
file did not exist; it then sent the in-process ``read_rpt_summary`` to an MCP
server and got a raw JSON-RPC "Unknown tool" error back.
"""

from __future__ import annotations

import shutil
from unittest import mock

from agentic_swmm.agent import tool_registry
from agentic_swmm.agent.tool_handlers.runtime_ops import _read_file_tool, _split_path_line
from agentic_swmm.agent.tool_registry import _call_mcp_tool_tool
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


class TestPathLineReferences:
    def test_a_grep_style_reference_reads_the_file_at_that_line(self):
        scratch = repo_root() / "runs" / "_test_path_line"
        shutil.rmtree(scratch, ignore_errors=True)
        scratch.mkdir(parents=True)
        target = scratch / "trace.jsonl"
        target.write_text("".join(f'{{"line": {i}}}\n' for i in range(1, 501)), encoding="utf-8")
        try:
            rel = str(target.relative_to(repo_root()))
            payload = _read_file_tool(ToolCall("read_file", {"path": f"{rel}:391"}), repo_root())
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        assert payload["ok"] is True, payload
        assert payload["excerpt"].startswith('{"line": 371}')
        assert '{"line": 391}' in payload["excerpt"]
        assert "requested line 391" in payload["summary"]

    def test_a_real_path_with_a_colon_is_not_split(self, tmp_path):
        assert _split_path_line("agentic_swmm/cli.py") == ("agentic_swmm/cli.py", None)
        assert _split_path_line("runs/x/trace.jsonl:12:4") == ("runs/x/trace.jsonl", 12)


class TestInProcessToolSentToMcp:
    def test_the_failure_names_the_route(self, monkeypatch):
        monkeypatch.setattr(tool_registry, "_mcp_server", lambda name: {"name": name, "command": "node", "args": []})

        def boom(*args, **kwargs):
            raise RuntimeError('{"code": -32603, "message": "Unknown tool: read_rpt_summary"}')

        monkeypatch.setattr(tool_registry.mcp_client, "call_tool", boom)
        payload = _call_mcp_tool_tool(
            ToolCall("call_mcp_tool", {"server": "swmm-runner", "tool": "read_rpt_summary", "arguments": {}}),
            repo_root(),
        )
        assert payload["ok"] is False
        assert "in-process tool" in payload["hint"]
        assert "call read_rpt_summary directly" in payload["hint"]

    def test_a_genuinely_unknown_tool_keeps_the_plain_failure(self, monkeypatch):
        monkeypatch.setattr(tool_registry, "_mcp_server", lambda name: {"name": name, "command": "node", "args": []})

        def boom(*args, **kwargs):
            raise RuntimeError('{"code": -32603, "message": "Unknown tool: frobnicate"}')

        monkeypatch.setattr(tool_registry.mcp_client, "call_tool", boom)
        payload = _call_mcp_tool_tool(
            ToolCall("call_mcp_tool", {"server": "swmm-runner", "tool": "frobnicate", "arguments": {}}),
            repo_root(),
        )
        assert payload["ok"] is False
        assert "in-process tool" not in (payload.get("hint") or "")
