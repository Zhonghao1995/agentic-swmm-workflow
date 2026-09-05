"""The approval question names what it approves, and a repository write is
never covered by the turn's chain grant (F-03, F-11, F-06; 2026-09-02).
"""

from __future__ import annotations

import unittest

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


def test_a_city_beside_a_placeholder_bbox_shows_the_city() -> None:
    """S44 (2026-09-03): the prompt read bbox [0.000, 0.000, 0.000, 0.000] for city=Toronto."""
    from agentic_swmm.agent.permissions import approval_detail

    detail = approval_detail(
        {"city": "Toronto", "bbox": [0, 0, 0, 0], "aoi_geojson": "", "start_date": "2023-11-01", "end_date": "2023-11-04"}
    )
    assert "city=Toronto" in detail
    assert "bbox [0.000" not in detail
    assert "2023-11-01..2023-11-04" in detail


def test_a_real_bbox_still_shows_the_box() -> None:
    from agentic_swmm.agent.permissions import approval_detail

    detail = approval_detail({"city": "Toronto", "bbox": [-79.38, 43.71, -79.37, 43.72]})
    assert detail.startswith("bbox [-79.380")


class SweepCostTests(unittest.TestCase):
    """F-160 (2026-09-05, S27 r3): a sweep says its cost before the approval."""

    RANGES = {"n_imperv": [0.010, 0.020], "pct_imperv": [60, 80]}

    def test_planned_sample_count_follows_the_sampling_rule(self) -> None:
        from agentic_swmm.agent.swmm_runtime.parameter_sweep import planned_sample_count

        one, two, three, four = ({"a": (0, 1)}, {"a": (0, 1), "b": (0, 1)}, {"a": (0, 1), "b": (0, 1), "c": (0, 1)}, {"a": (0, 1), "b": (0, 1), "c": (0, 1), "d": (0, 1)})
        self.assertEqual(planned_sample_count(one), 5)
        self.assertEqual(planned_sample_count(two), 25)
        self.assertEqual(planned_sample_count(three), 27)
        self.assertEqual(planned_sample_count(four), 36)
        self.assertEqual(planned_sample_count(two, 10), 10)
        self.assertEqual(planned_sample_count(two, 1), 2)

    def test_the_cost_uses_the_baseline_runs_elapsed_time(self) -> None:
        from tempfile import TemporaryDirectory

        from agentic_swmm.agent.permissions import approval_detail, sweep_cost_phrase

        with TemporaryDirectory() as tmp:
            run = Path(tmp) / "runs" / "2026-09-05" / "x_run"
            (run / "06_runner").mkdir(parents=True)
            (run / "05_builder").mkdir()
            (run / "06_runner" / "model.rpt").write_text("...\n  Total elapsed time: 00:00:33\n", encoding="utf-8")
            args = {"inp_path": str(run / "05_builder" / "model.inp"), "node": "OUT_0", "ranges": self.RANGES}
            self.assertEqual(sweep_cost_phrase(args), "26 SWMM runs, about 14 min (one run took 33 s)")
            detail = approval_detail(args)
            self.assertTrue(detail.startswith("26 SWMM runs, about 14 min"), detail)
            self.assertIn("node=OUT_0", detail)
            self.assertEqual(sweep_cost_phrase({**args, "n_samples": 5}), "6 SWMM runs, about 3 min (one run took 33 s)")

    def test_without_a_baseline_rpt_only_the_count_is_said(self) -> None:
        from agentic_swmm.agent.permissions import sweep_cost_phrase

        self.assertEqual(sweep_cost_phrase({"inp_path": "/nowhere/model.inp", "ranges": self.RANGES}), "26 SWMM runs")
        self.assertEqual(sweep_cost_phrase({"inp_path": "/nowhere/model.inp"}), "")

    def test_a_sub_second_run_gives_no_time_estimate(self) -> None:
        from tempfile import TemporaryDirectory

        from agentic_swmm.agent.permissions import _elapsed_seconds_from_rpt, sweep_cost_phrase

        with TemporaryDirectory() as tmp:
            rpt = Path(tmp) / "06_runner" / "model.rpt"
            rpt.parent.mkdir()
            rpt.write_text("  Total elapsed time: < 1 sec\n", encoding="utf-8")
            self.assertIsNone(_elapsed_seconds_from_rpt(rpt))
            self.assertEqual(sweep_cost_phrase({"run_dir": tmp, "ranges": self.RANGES}), "26 SWMM runs")

