"""A miss inside a chat folder points at the run the chat is about (F-128).

Live finding 2026-09-03 (scenario S54 r3, turn 2): asked what the expert
review recorded, the planner read
<chat folder>/09_audit/experiment_provenance.json, got "file not found",
and told the user that no decision file exists, while the run's own
09_audit held the record it had written one turn earlier.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent import error_remediation
from agentic_swmm.agent.tool_handlers._shared import _missing_file_failure
from agentic_swmm.agent.types import ToolCall


def _day(tmp_path: Path) -> tuple[Path, Path]:
    day = tmp_path / "runs" / "2026-09-03"
    run = day / "150906_fetch-swmm-model_run"
    (run / "09_audit").mkdir(parents=True)
    (run / "09_audit" / "experiment_provenance.json").write_text("{}\n", encoding="utf-8")
    chat = day / "190946_150906_fetch-swmm-model_run_chat"
    (chat / "_agent").mkdir(parents=True)
    return run, chat


def test_the_chat_folder_names_its_run(tmp_path: Path) -> None:
    run, chat = _day(tmp_path)
    assert error_remediation.anchored_run_for(chat / "09_audit" / "x.json") == run
    assert error_remediation.anchored_run_for(run / "09_audit") is None
    plain = tmp_path / "runs" / "2026-09-03" / "175323_you-remember-victoria_chat"
    plain.mkdir()
    assert error_remediation.anchored_run_for(plain / "chat_note.md") is None


def test_a_missing_file_in_the_chat_folder_points_at_the_run_copy(tmp_path: Path) -> None:
    run, chat = _day(tmp_path)
    requested = chat / "09_audit" / "experiment_provenance.json"
    err = error_remediation.file_resolution_error("file not found", requested=requested, search_dir=requested.parent)
    assert err.hint is not None
    assert str(run) in err.hint
    assert "exists: read that" in err.hint
    assert "experiment_provenance.json" in err.hint


def test_a_file_missing_in_both_says_so(tmp_path: Path) -> None:
    run, chat = _day(tmp_path)
    requested = chat / "06_runner" / "model.rpt"
    err = error_remediation.file_resolution_error("file not found", requested=requested, search_dir=requested.parent)
    assert err.hint is not None
    assert "does not exist there either" in err.hint
    assert str(run) in err.hint


def test_the_read_file_fallback_carries_the_anchor(tmp_path: Path) -> None:
    run, chat = _day(tmp_path)
    requested = chat / "09_audit" / "experiment_provenance.json"
    call = ToolCall(name="read_file", args={"path": str(requested)})
    result = _missing_file_failure(call, requested, ".json")
    assert result["ok"] is False
    assert str(run) in str(result.get("hint"))


def test_a_run_folder_miss_gets_no_anchor(tmp_path: Path) -> None:
    run, _chat = _day(tmp_path)
    requested = run / "06_runner" / "model.rpt"
    err = error_remediation.file_resolution_error("file not found", requested=requested, search_dir=requested.parent)
    assert err.hint is None or "chat folder" not in err.hint
