"""Integration tests for the recall tools in ``tool_registry`` (PRD M1, M6).

These tests are pure: no LLM. They invoke the registered tool handlers
directly against a seeded store (the suite's isolated
``AISWMM_MEMORY_DIR``) and assert that:

- both recall tools appear in the registry schema dump,
- their descriptions carry the literal ``USE WHEN`` / ``DO NOT USE WHEN``
  routing text (PRD M7.2),
- ``ToolSpec`` exposes an ``is_read_only`` flag and the recall tools are
  marked ``True`` (PRD M1/M6 contract),
- ``recall_memory`` results are wrapped in ``<memory-context source="store" ...>``
  (PRD M7.1 integration; the store replaced the lessons file in the
  memory simplification, 2026-09-26).
"""

from __future__ import annotations

from pathlib import Path


def test_recall_tools_appear_in_registry() -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    names = AgentToolRegistry().sorted_names()
    assert "recall_memory" in names
    assert "recall_session_history" in names
    assert "recall_memory_search" not in names


def test_recall_tool_descriptions_carry_routing_phrases() -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    registry = AgentToolRegistry()
    schemas = {schema["name"]: schema for schema in registry.schemas()}

    rm = schemas["recall_memory"]
    assert "USE WHEN" in rm["description"]
    assert "DO NOT USE WHEN" in rm["description"]
    assert "negative lessons" in rm["description"]
    assert set(rm["parameters"]["properties"]) == {"query", "case_name", "limit"}

    rsh = schemas["recall_session_history"]
    assert "USE WHEN" in rsh["description"]
    assert "DO NOT USE WHEN" in rsh["description"]


def test_toolspec_exposes_is_read_only_field() -> None:
    from agentic_swmm.agent.tool_registry import ToolSpec, _build_tools

    tools = _build_tools()
    assert hasattr(ToolSpec, "__dataclass_fields__")
    assert "is_read_only" in ToolSpec.__dataclass_fields__
    assert tools["recall_memory"].is_read_only is True
    assert tools["recall_session_history"].is_read_only is True
    # A non-read-only tool must keep its existing default.
    assert tools["run_swmm_inp"].is_read_only is False


def _seed_store() -> None:
    from agentic_swmm.memory.jsonl_store import append_row
    from agentic_swmm.utils.paths import resolve_memory_dir

    append_row(resolve_memory_dir() / "run_failures.jsonl", {
        "schema_version": "1.1", "run_id": "s1", "tool": "run_allowed_command", "failure_class": "tool_error",
        "summary": "command is not allowlisted", "recorded_at": "2026-09-03T14:59:56Z",
        "pattern": "run_allowed_command:tool_error:command is not allowlisted",
        "fix": "read_rpt_summary(rpt_path=r.rpt) instead", "fix_tool": "read_rpt_summary",
    })


def test_recall_memory_handler_wraps_payload_in_memory_context(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    _seed_store()
    result = AgentToolRegistry().execute(
        ToolCall("recall_memory", {"query": "allowlisted command"}),
        session_dir=tmp_path / "session",
    )

    assert result["ok"] is True
    excerpt = result.get("excerpt", "")
    assert '<memory-context source="store"' in excerpt
    assert "fixed by: read_rpt_summary" in excerpt
    assert result["results"][0]["kind"] == "failure"
    assert result["summary"].startswith("recall_memory: 1 hit(s)")


def test_recall_memory_handler_returns_empty_for_unknown_query(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    _seed_store()
    result = AgentToolRegistry().execute(
        ToolCall("recall_memory", {"query": "sediment washoff"}),
        session_dir=tmp_path / "session",
    )

    assert result["ok"] is True
    assert result["results"] == []
    # No match: handler returns a wrapped empty payload, with a clear
    # summary so the planner can decide what to do next.
    assert "no match" in result["summary"].lower()
