"""The typed network_qa checks an INP as well as a network JSON (F-67b).

Live finding 2026-09-02 (scenario S18 turn 2): the MCP qa accepts inpPath,
the typed surface only knew network_json, so an INP check through the typed
tool failed with "network_json must end with .json" and the planner fell
back to the upstream validation file.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers.swmm_network import _network_qa_args
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

INP = "examples/tecnopolo/tecnopolo_r1_199401.inp"


def test_an_inp_maps_to_the_mcp_inp_path(tmp_path):
    mapped = _network_qa_args(ToolCall(name="network_qa", args={"inp_path": INP}), tmp_path)
    assert mapped == {"inpPath": str(repo_root() / INP)}


def test_a_network_json_still_maps_as_before(tmp_path):
    target = repo_root() / "runs" / "f67b_tmp" / "network.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text("{}")
        mapped = _network_qa_args(ToolCall(name="network_qa", args={"network_json": "runs/f67b_tmp/network.json"}), tmp_path)
        assert mapped == {"networkJsonPath": str(target)}
    finally:
        import shutil

        shutil.rmtree(target.parent, ignore_errors=True)


def test_neither_or_both_is_refused(tmp_path):
    neither = _network_qa_args(ToolCall(name="network_qa", args={}), tmp_path)
    both = _network_qa_args(ToolCall(name="network_qa", args={"inp_path": INP, "network_json": "x.json"}), tmp_path)
    assert neither["ok"] is False and "exactly one" in neither["summary"]
    assert both["ok"] is False and "exactly one" in both["summary"]


def test_the_schema_offers_inp_path_and_requires_nothing_up_front():
    schema = AgentToolRegistry().schemas({"network_qa"})[0]["parameters"]
    assert "inp_path" in schema["properties"]
    assert schema.get("required", []) == []
