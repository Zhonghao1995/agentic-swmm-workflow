"""Agent-authored code stays under the run it serves (F-60).

Live finding 2026-09-02 (scenario S11 r2): with no typed ensemble tool the
planner wrote scripts/run_peak_outflow_uncertainty.mjs into the repository
through apply_patch and ran it. The consent line named the file, but a
modeling turn must not author code into the product tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent import permissions
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(permissions.REPO_WRITES_ENV, raising=False)


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("scripts/run_peak_outflow_uncertainty.mjs", False),
        ("agentic_swmm/agent/new_tool.py", False),
        ("tools/helper.sh", False),
        ("runs/x/_agent/scripts/ensemble.mjs", True),
        ("runs/x/09_audit/notes.md", True),
        ("docs/plan.md", True),
        ("examples/x/space.json", True),
    ],
)
def test_write_policy(rel, expected):
    assert permissions.is_allowed_write_path(repo_root() / rel) is expected


def test_agent_scratch_is_neither_evidence_nor_product_code():
    scratch = repo_root() / "runs" / "x" / "_agent" / "scripts" / "ensemble.mjs"
    assert permissions.is_agent_scratch_path(scratch)
    assert not permissions.is_code_write_into_product_tree(scratch)
    assert permissions.is_evidence_path(scratch), "still under runs/ for every other purpose"


def test_the_override_lets_a_developer_write_code(monkeypatch):
    monkeypatch.setenv(permissions.REPO_WRITES_ENV, "1")
    assert permissions.is_allowed_write_path(repo_root() / "scripts" / "x.mjs") is True


ENVELOPE = """*** Begin Patch
*** Add File: {path}
+console.log("ensemble");
*** End Patch
"""


def test_apply_patch_refuses_repo_code_with_the_hint(tmp_path):
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall(name="apply_patch", args={"patch": ENVELOPE.format(path="scripts/f60_probe.mjs")}), tmp_path
    )
    assert result["ok"] is False
    assert "code into the product tree" in result["summary"]
    assert "_agent/scripts" in result["hint"]
    assert not (repo_root() / "scripts" / "f60_probe.mjs").exists()


def test_apply_patch_accepts_agent_scratch_without_the_evidence_override(tmp_path):
    rel = "runs/f60_tmp_run/_agent/scripts/ensemble.mjs"
    # The run folder exists before the patch: since F-142 (2026-09-04, S63) a
    # patch never mints a run folder, it may only write scratch into one that
    # a tool already opened.
    (repo_root() / "runs" / "f60_tmp_run").mkdir(parents=True, exist_ok=True)
    target = repo_root() / rel
    try:
        registry = AgentToolRegistry()
        result = registry.execute(ToolCall(name="apply_patch", args={"patch": ENVELOPE.format(path=rel)}), tmp_path)
        assert result["ok"] is True, result
        assert target.exists()
    finally:
        import shutil

        shutil.rmtree(repo_root() / "runs" / "f60_tmp_run", ignore_errors=True)
