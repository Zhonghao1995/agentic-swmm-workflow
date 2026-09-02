"""Earlier turns' reports are kept, not overwritten (F-20, 2026-09-02)."""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.reporting import write_report


def _write(session_dir: Path, goal: str) -> Path:
    return write_report(session_dir, goal, plan=[], results=[], dry_run=True, allowed_tools=set(), planner="llm",
                        final_text=f"answer for {goal}")


def test_the_root_report_is_the_latest_and_earlier_turns_live_under_agent(tmp_path):
    session = tmp_path / "run"
    session.mkdir()
    _write(session, "turn one: fetch and audit")
    _write(session, "turn two: busiest conduits")
    _write(session, "turn three: node flooding")
    latest = (session / "final_report.md").read_text(encoding="utf-8")
    assert "turn three" in latest
    kept = sorted(p.name for p in (session / "_agent").glob("final_report_turn*.md"))
    assert kept == ["final_report_turn1.md", "final_report_turn2.md"]
    assert "turn one" in (session / "_agent" / "final_report_turn1.md").read_text(encoding="utf-8")
    assert "turn two" in (session / "_agent" / "final_report_turn2.md").read_text(encoding="utf-8")


def test_a_single_turn_session_has_no_rotated_copies(tmp_path):
    session = tmp_path / "run"
    session.mkdir()
    _write(session, "only turn")
    assert not list((session / "_agent").glob("final_report_turn*.md")) if (session / "_agent").exists() else True
