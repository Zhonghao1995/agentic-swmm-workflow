from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "skills/swmm-end-to-end/SKILL.md"


def test_end_to_end_skill_requires_mcp_first_framework_smoke_runs() -> None:
    text = SKILL.read_text(encoding="utf-8")

    assert "MCP-first framework smoke test mode" in text
    assert "Do not bypass MCP tool contracts by calling the underlying Python scripts as the primary path" in text
    assert "mcp_tool_calls" in text
    assert "missing_or_fallback_inputs" in text
    assert "tool_transport" in text
