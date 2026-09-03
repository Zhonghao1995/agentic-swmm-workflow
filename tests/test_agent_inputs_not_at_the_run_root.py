"""Agent-authored inputs go under _agent/inputs/, not the run root (F-61).

Live finding 2026-09-02 (scenario S05 r2): apply_patch wrote
manning_n_search_space.json directly into the run folder, which the
canonical layout reserves for the product's own files.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentic_swmm.agent import permissions
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root

ENVELOPE = """*** Begin Patch
*** Add File: {path}
+{{"manning_n": [0.01, 0.02]}}
*** End Patch
"""


@pytest.fixture
def run_dir():
    run = repo_root() / "runs" / "f61_tmp_run"
    run.mkdir(parents=True, exist_ok=True)
    try:
        yield run
    finally:
        shutil.rmtree(run, ignore_errors=True)


def test_a_new_file_at_the_run_root_is_refused_with_the_hint(run_dir):
    rel = "runs/f61_tmp_run/manning_n_search_space.json"
    result = AgentToolRegistry().execute(
        ToolCall(name="apply_patch", args={"patch": ENVELOPE.format(path=rel), "allow_evidence_edits": True}), run_dir
    )
    assert result["ok"] is False
    assert "run root" in result["summary"]
    assert "_agent/inputs" in result["hint"]
    assert not (repo_root() / rel).exists()


def test_agent_inputs_need_no_evidence_override(run_dir):
    rel = "runs/f61_tmp_run/_agent/inputs/manning_n_search_space.json"
    result = AgentToolRegistry().execute(ToolCall(name="apply_patch", args={"patch": ENVELOPE.format(path=rel)}), run_dir)
    assert result["ok"] is True, result
    assert (repo_root() / rel).exists()


def test_canonical_root_files_are_not_caught(run_dir):
    assert not permissions.is_new_file_at_run_root(run_dir / "README.md", run_dir)
    assert permissions.is_new_file_at_run_root(run_dir / "search_space.json", run_dir)
    assert not permissions.is_new_file_at_run_root(run_dir / "03_config" / "x.json", run_dir)


def test_scratch_covers_scripts_and_inputs():
    assert permissions.is_agent_scratch_path(repo_root() / "runs/x/_agent/scripts/a.mjs")
    assert permissions.is_agent_scratch_path(repo_root() / "runs/x/_agent/inputs/a.json")
    assert not permissions.is_agent_scratch_path(repo_root() / "runs/x/_agent/agent_trace.jsonl")
