"""The approval question names what it approves, and a repository write is
never covered by the turn's chain grant (F-03, F-11, F-06; 2026-09-02).
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent import permissions
from agentic_swmm.agent.executor import NEVER_CHAINED, AgentExecutor
from agentic_swmm.agent.permissions import approval_detail
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.types import ToolCall


class TestApprovalDetail:
    def test_a_command_is_shown_verbatim(self):
        assert approval_detail({"command": ["python", "-c", "print(1)"]}) == "python -c print(1)"

    def test_a_patch_names_the_files_it_writes(self):
        patch = "*** Begin Patch\n*** Add File: scripts/x.mjs\n+a\n*** Update File: README.md\n*** End Patch"
        assert approval_detail({"patch": patch}) == "writes scripts/x.mjs, README.md"

    def test_a_fetch_names_the_area_and_the_window(self):
        detail = approval_detail({"bbox": [-123.37, 48.425, -123.36, 48.432], "start_date": "2023-11-01",
                                  "end_date": "2023-11-04", "run_dir": "/x/runs/a"})
        assert detail == "bbox [-123.370, 48.425, -123.360, 48.432], 2023-11-01..2023-11-04"

    def test_a_run_names_the_model(self):
        detail = approval_detail({"inp_path": "/Users/me/repo/runs/2026-09-02/x_run/05_builder/model.inp", "node": "auto"})
        assert detail == "runs/2026-09-02/x_run/05_builder/model.inp"

    def test_long_details_are_clipped_and_empty_args_give_nothing(self):
        assert approval_detail({}) == "" and approval_detail(None) == ""
        assert len(approval_detail({"command": ["x" * 200]})) == 90


class _Tty:
    def isatty(self):
        return True

    def fileno(self):
        raise OSError("no fileno in tests")


class TestQuestionText:
    def test_the_question_carries_the_detail(self, monkeypatch):
        seen: list[str] = []
        monkeypatch.delenv("AISWMM_AUTO_APPROVE", raising=False)
        monkeypatch.setattr(permissions.sys, "stdin", _Tty())
        monkeypatch.setattr("builtins.input", lambda q: seen.append(q) or "y")
        decision = permissions.request_approval("fetch_swmm_from_canada", "bbox [1, 2, 3, 4]")
        assert decision.approved
        assert seen == ["Run fetch_swmm_from_canada (bbox [1, 2, 3, 4])? [Y/n] "]

    def test_no_detail_keeps_the_old_question(self, monkeypatch):
        seen: list[str] = []
        monkeypatch.delenv("AISWMM_AUTO_APPROVE", raising=False)
        monkeypatch.setattr(permissions.sys, "stdin", _Tty())
        monkeypatch.setattr("builtins.input", lambda q: seen.append(q) or "")
        permissions.request_approval("map_run")
        assert seen == ["Run map_run? [Y/n] "]


class _Registry:
    def is_read_only(self, name):
        return False

    def execute(self, call, session_dir):
        return {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}


class TestRepositoryWritesAreNeverChained:
    def test_apply_patch_is_in_the_never_chained_set(self):
        assert "apply_patch" in NEVER_CHAINED

    def test_a_yes_for_a_command_does_not_cover_apply_patch(self):
        with TemporaryDirectory() as tmp:
            ex = AgentExecutor(_Registry(), session_dir=Path(tmp), trace_path=Path(tmp) / "t.jsonl",
                               dry_run=False, profile=Profile.QUICK, verbose=False)
            with mock.patch("agentic_swmm.agent.executor.permissions.request_approval") as approval:
                approval.return_value = mock.Mock(approved=True, needs_guidance=False)
                ex.execute(ToolCall(name="run_allowed_command", args={"command": ["pytest"]}))
                ex.execute(ToolCall(name="apply_patch", args={"patch": "*** Add File: a.py"}))
                ex.execute(ToolCall(name="run_swmm_inp", args={}))
            # command (prompted, arms the chain), apply_patch (prompted anyway), run (chained)
            assert approval.call_count == 2
            assert approval.call_args_list[1].args[0] == "apply_patch"
            assert "writes a.py" in approval.call_args_list[1].args[1]

    def test_a_yes_for_apply_patch_does_not_arm_the_chain(self):
        with TemporaryDirectory() as tmp:
            ex = AgentExecutor(_Registry(), session_dir=Path(tmp), trace_path=Path(tmp) / "t.jsonl",
                               dry_run=False, profile=Profile.QUICK, verbose=False)
            with mock.patch("agentic_swmm.agent.executor.permissions.request_approval") as approval:
                approval.return_value = mock.Mock(approved=True, needs_guidance=False)
                ex.execute(ToolCall(name="apply_patch", args={"patch": "*** Add File: a.py"}))
                ex.execute(ToolCall(name="run_swmm_inp", args={}))
            assert approval.call_count == 2


class TestWelcomeLine:
    def test_no_case_means_no_case_clause(self):
        from agentic_swmm.agent.welcome import _format_last_session_line

        line = _format_last_session_line({"case_name": None})
        assert line.startswith("Last session")
        assert "unknown" not in line and 'case ""' not in line

    def test_a_known_case_is_still_named(self):
        from agentic_swmm.agent.welcome import _format_last_session_line

        assert 'case "downtown-victoria-bc"' in _format_last_session_line({"case_name": "downtown-victoria-bc"})
