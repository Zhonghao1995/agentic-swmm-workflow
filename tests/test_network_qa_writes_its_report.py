"""The typed network_qa writes the report it is cited for (F-68).

Live finding 2026-09-02 (scenario S18 r2): the planner reported "Artifact:
05_builder/network_qa.json" for a file that did not exist; the QA JSON had
only come back inside the MCP content and the typed tool ignored
report_json.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentic_swmm.agent.tool_handlers import swmm_network
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

QA = {"ok": True, "summary": {"junction_count": 304}, "issue_count": 6, "issues": [{"severity": "warning", "node": "N5"}]}


def _routed_ok(call, session_dir):
    return {"tool": call.name, "args": call.args, "ok": True, "results": {"content": [{"type": "text", "text": json.dumps(QA)}]}, "summary": "called swmm-network.qa"}


def _routed_failed(call, session_dir):
    return {"tool": call.name, "args": call.args, "ok": False, "summary": "MCP tools/call failed"}


def test_the_report_lands_in_the_runs_audit_stage_by_default(tmp_path):
    handler = swmm_network._persist_qa_report(_routed_ok)
    result = handler(ToolCall(name="network_qa", args={"inp_path": "x.inp"}), tmp_path)
    target = Path(result["report_json"])
    assert target == tmp_path / "09_audit" / "network_qa.json"
    assert json.loads(target.read_text())["issue_count"] == 6
    assert result["issue_count"] == 6 and result["qa_ok"] is True
    assert "report written to" in result["summary"]


def test_a_requested_report_json_is_honoured():
    handler = swmm_network._persist_qa_report(_routed_ok)
    rel = "runs/f68_tmp_run/09_audit/my_qa.json"
    target = repo_root() / rel
    try:
        result = handler(ToolCall(name="network_qa", args={"inp_path": "x.inp", "report_json": rel}), repo_root() / "runs" / "f68_tmp_run")
        assert Path(result["report_json"]) == target
        assert target.exists()
    finally:
        import shutil

        shutil.rmtree(repo_root() / "runs" / "f68_tmp_run", ignore_errors=True)


def test_a_failed_call_is_passed_through_untouched(tmp_path):
    handler = swmm_network._persist_qa_report(_routed_failed)
    result = handler(ToolCall(name="network_qa", args={"inp_path": "x.inp"}), tmp_path)
    assert result["ok"] is False and "report_json" not in result
    assert not (tmp_path / "09_audit").exists()
