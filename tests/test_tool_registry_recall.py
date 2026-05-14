"""Integration tests for recall tools in ``tool_registry`` (PRD M1, M6).

These tests are pure: no LLM, no embedding model. They invoke the
registered tool handlers directly, using fixture lessons / corpus
files written into the repository tree, and assert that:

- both tools appear in the registry schema dump,
- their descriptions carry the literal ``USE WHEN`` / ``DO NOT USE WHEN``
  routing text (PRD M7.2),
- ``ToolSpec`` exposes an ``is_read_only`` flag and the two recall
  tools are marked ``True`` (PRD M1/M6 contract),
- ``recall_memory`` results are wrapped in ``<memory-context source='lessons' ...>``
  (PRD M7.1 integration).
"""

from __future__ import annotations

from pathlib import Path


def test_recall_memory_and_search_appear_in_registry() -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    names = AgentToolRegistry().sorted_names()
    assert "recall_memory" in names
    assert "recall_memory_search" in names


def test_recall_tool_descriptions_carry_routing_phrases() -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry

    registry = AgentToolRegistry()
    schemas = {schema["name"]: schema for schema in registry.schemas()}

    rm = schemas["recall_memory"]
    assert "USE WHEN" in rm["description"]
    assert "DO NOT USE WHEN" in rm["description"]
    assert "failure_pattern" in rm["description"]

    rms = schemas["recall_memory_search"]
    assert "USE WHEN" in rms["description"]
    assert "DO NOT USE WHEN" in rms["description"]
    assert "top-k" in rms["description"] or "top_k" in rms["description"]


def test_toolspec_exposes_is_read_only_field() -> None:
    from agentic_swmm.agent.tool_registry import ToolSpec, _build_tools

    tools = _build_tools()
    assert hasattr(ToolSpec, "__dataclass_fields__")
    assert "is_read_only" in ToolSpec.__dataclass_fields__
    assert tools["recall_memory"].is_read_only is True
    assert tools["recall_memory_search"].is_read_only is True
    # A non-read-only tool must keep its existing default.
    assert tools["run_swmm_inp"].is_read_only is False


def test_recall_memory_handler_wraps_payload_in_memory_context(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    # Synthesize a lessons fixture and point the env override there.
    lessons_dir = tmp_path / "memory" / "modeling-memory"
    lessons_dir.mkdir(parents=True)
    lessons_path = lessons_dir / "lessons_learned.md"
    lessons_path.write_text(
        "# Lessons\n\n## fixture_pattern\n\nA lesson body.\n", encoding="utf-8"
    )

    import os

    os.environ["AISWMM_LESSONS_PATH"] = str(lessons_path)
    try:
        registry = AgentToolRegistry()
        result = registry.execute(
            ToolCall("recall_memory", {"pattern": "fixture_pattern"}),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_LESSONS_PATH", None)

    assert result["ok"] is True
    excerpt = result.get("excerpt", "")
    assert '<memory-context source="lessons"' in excerpt
    assert "A lesson body." in excerpt
    assert "fixture_pattern" in excerpt


def test_recall_memory_handler_returns_empty_for_unknown_pattern(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    lessons_dir = tmp_path / "memory" / "modeling-memory"
    lessons_dir.mkdir(parents=True)
    lessons_path = lessons_dir / "lessons_learned.md"
    lessons_path.write_text("# Lessons\n\n## other_pattern\n\nbody.\n", encoding="utf-8")

    import os

    os.environ["AISWMM_LESSONS_PATH"] = str(lessons_path)
    try:
        registry = AgentToolRegistry()
        result = registry.execute(
            ToolCall("recall_memory", {"pattern": "missing"}),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_LESSONS_PATH", None)

    assert result["ok"] is True
    # No match: handler returns a wrapped empty payload, with a clear
    # summary so the planner can decide what to do next.
    assert "no match" in result["summary"].lower()
