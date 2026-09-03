"""A tool's "file not found" names the files that do exist.

Live test 2026-09-03 (S40 r4): the planner asked read_rpt_summary for
06_run/model.rpt (the stage is 06_runner) and got a bare "file not found",
then browsed for the real path.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from agentic_swmm.agent.tool_handlers.swmm_rpt import _read_rpt_summary_tool
from agentic_swmm.agent.types import ToolCall


def _call(rpt_path: str) -> ToolCall:
    return ToolCall(name="read_rpt_summary", args={"rpt_path": rpt_path, "section": "Link Flow Summary"})


def test_a_guessed_stage_name_gets_the_real_report_named(tmp_path: Path) -> None:
    run_dir = tmp_path / "145956_downtown-regina_run"
    (run_dir / "06_runner").mkdir(parents=True)
    (run_dir / "06_runner" / "model.rpt").write_text("  Flow Units ............... CMS\n", encoding="utf-8")
    with mock.patch("agentic_swmm.agent.tool_handlers._shared.repo_root", return_value=tmp_path):
        result = _read_rpt_summary_tool(_call(str(run_dir / "06_run" / "model.rpt")), tmp_path)
    assert result["ok"] is False
    assert "file not found" in result["summary"]
    assert "06_runner/model.rpt" in (result.get("hint") or "")


def test_a_wrong_name_in_an_existing_directory_gets_a_did_you_mean(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "06_runner").mkdir(parents=True)
    (run_dir / "06_runner" / "model.rpt").write_text("", encoding="utf-8")
    with mock.patch("agentic_swmm.agent.tool_handlers._shared.repo_root", return_value=tmp_path):
        result = _read_rpt_summary_tool(_call(str(run_dir / "06_runner" / "modle.rpt")), tmp_path)
    assert result["ok"] is False
    assert "model.rpt" in (result.get("hint") or "")
