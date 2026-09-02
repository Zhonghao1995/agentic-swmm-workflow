"""read_file / search_files do not treat the product's source as evidence (F-54).

Live finding 2026-09-02 (scenario S11, "how uncertain is the peak"): 32 of
34 tool calls read agentic_swmm/** and skills/*/scripts/*.py to learn how a
tool worked, then the planner asked the user a questionnaire.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.tool_handlers import runtime_ops
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    monkeypatch.delenv(runtime_ops.SOURCE_READS_ENV, raising=False)


@pytest.mark.parametrize(
    "rel, expected",
    [
        ("agentic_swmm/agent/planner.py", True),
        ("skills/swmm-uncertainty/scripts/uncertainty_propagate.py", True),
        ("tests/test_x.py", True),
        ("skills/swmm-uncertainty/SKILL.md", False),
        ("skills/swmm-uncertainty/examples/fuzzy_space.json", False),
        ("runs/x/06_runner/model.rpt", False),
        ("examples/calibration/patch_map.json", False),
    ],
)
def test_is_product_source(rel, expected):
    assert runtime_ops.is_product_source(repo_root() / rel) is expected


def test_read_file_refuses_source_with_a_hint():
    registry = AgentToolRegistry()
    result = registry.execute(ToolCall(name="read_file", args={"path": "agentic_swmm/agent/planner.py"}), Path("."))
    assert result["ok"] is False
    assert "product source" in result["summary"]
    assert "select_skill" in result["hint"]


def test_read_file_still_reads_skill_docs_and_examples():
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall(name="read_file", args={"path": "skills/swmm-uncertainty/examples/fuzzy_space.json"}), Path(".")
    )
    assert result["ok"] is True


def test_the_override_lets_a_developer_read_source(monkeypatch):
    monkeypatch.setenv(runtime_ops.SOURCE_READS_ENV, "1")
    registry = AgentToolRegistry()
    result = registry.execute(ToolCall(name="read_file", args={"path": "agentic_swmm/agent/planner.py"}), Path("."))
    assert result["ok"] is True


def test_search_skips_source_and_says_so():
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall(name="search_files", args={"query": "def run", "glob": "agentic_swmm/agent/*.py", "max_results": 5}),
        Path("."),
    )
    assert result["ok"] is True
    assert result["results"] == []
    assert result["skipped_source_files"] > 0
    assert "select_skill" in result["hint"]


def test_search_of_examples_is_unaffected():
    registry = AgentToolRegistry()
    result = registry.execute(
        ToolCall(name="search_files", args={"query": "pct_imperv", "glob": "skills/swmm-uncertainty/examples/*.json", "max_results": 5}),
        Path("."),
    )
    assert result["ok"] is True
    assert result["skipped_source_files"] == 0
