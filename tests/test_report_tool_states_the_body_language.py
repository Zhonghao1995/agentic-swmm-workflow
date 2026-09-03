"""The report tool says which language its body is in.

Live test 2026-09-03 (S38): asked for a Chinese Word report, the product
wrote the English template body under a Chinese cover title and the
assistant announced a Chinese report. The tool now tells the planner, in
its description and in its result, that the body follows the English
template so the final answer can be honest about it.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_handlers.swmm_report import BODY_LANGUAGE_NOTE, tool_specs


def test_spec_tells_the_planner_the_body_is_the_english_template() -> None:
    spec = next(s for s in tool_specs() if s.name == "generate_report")
    assert "English report template" in spec.description
    assert "another language" in spec.description


def test_body_language_note_names_the_template_and_the_free_title() -> None:
    assert "English report template" in BODY_LANGUAGE_NOTE
    assert "cover title" in BODY_LANGUAGE_NOTE


def test_success_result_carries_the_body_language_note(monkeypatch, tmp_path) -> None:
    from pathlib import Path

    from agentic_swmm.agent.tool_handlers import swmm_report
    from agentic_swmm.agent.types import ToolCall

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setattr(swmm_report, "_resolve_run_dir", lambda call, key: run_dir)
    monkeypatch.setattr(swmm_report, "resource_path", lambda *parts: Path("/tmp/generate_report.py"))
    monkeypatch.setattr(
        swmm_report, "_run_script_tool", lambda call, session_dir, cli_args: {"ok": True, "summary": "Report written to: x.docx"}
    )
    result = swmm_report._generate_report_tool(ToolCall(name="generate_report", args={"run_dir": str(run_dir)}), tmp_path)
    assert result["ok"] is True
    assert result["summary"].startswith("Report written to: x.docx (")
    assert "English report template" in result["summary"]
