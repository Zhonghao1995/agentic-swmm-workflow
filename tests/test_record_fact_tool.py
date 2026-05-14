"""Handler test for the ``record_fact`` tool.

Calling the tool must append a ``§``-delimited block to
``facts_staging.md`` (not to ``facts.md``) and leave ``facts.md``
untouched. The tool is *not* read-only — it writes a tracked-adjacent
file, so it must not be auto-approved by ``Profile.QUICK``.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_record_fact_appends_to_staging_not_facts(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    curated_dir = tmp_path / "curated"
    os.environ["AISWMM_FACTS_DIR"] = str(curated_dir)
    try:
        registry = AgentToolRegistry()
        assert registry.is_read_only("record_fact") is False
        result = registry.execute(
            ToolCall("record_fact", {"text": "user prefers metric units"}),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_FACTS_DIR", None)

    assert result["ok"] is True
    staging = curated_dir / "facts_staging.md"
    facts = curated_dir / "facts.md"
    assert staging.exists()
    content = staging.read_text(encoding="utf-8")
    assert "user prefers metric units" in content
    assert content.count("§") >= 2  # opening and closing delimiter
    # facts.md is *not* touched by record_fact — staging is the only sink.
    assert not facts.exists()


def test_record_fact_rejects_empty_text(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    os.environ["AISWMM_FACTS_DIR"] = str(tmp_path / "curated")
    try:
        registry = AgentToolRegistry()
        result = registry.execute(
            ToolCall("record_fact", {"text": "   "}),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_FACTS_DIR", None)
    assert result["ok"] is False
    assert "text is required" in result["summary"]


def test_record_fact_carries_source_session_id(tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_registry import AgentToolRegistry
    from agentic_swmm.agent.types import ToolCall

    curated_dir = tmp_path / "curated"
    os.environ["AISWMM_FACTS_DIR"] = str(curated_dir)
    try:
        registry = AgentToolRegistry()
        registry.execute(
            ToolCall(
                "record_fact",
                {
                    "text": "the project standard outfall is O1",
                    "source_session_id": "20260513_120000_todcreek_run",
                },
            ),
            session_dir=tmp_path / "session",
        )
    finally:
        os.environ.pop("AISWMM_FACTS_DIR", None)
    content = (curated_dir / "facts_staging.md").read_text(encoding="utf-8")
    assert "source_session: 20260513_120000_todcreek_run" in content
    assert "the project standard outfall is O1" in content
