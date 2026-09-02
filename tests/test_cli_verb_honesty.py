"""Four deterministic verbs as a novice met them (S09, 2026-09-02).

`aiswmm review` wrapped its FAIL verdict in "error: command failed";
`aiswmm compare` rejected the flag spellings `audit` teaches; `aiswmm trace`
on a CLI run printed one bare "?" row; `aiswmm runs tidy --dry-run` printed
438 lines. None of these involve the LLM.
"""

from __future__ import annotations

import argparse
import json
from argparse import Namespace
from pathlib import Path

import pytest

from agentic_swmm.commands import compare, review, runs_tidy, trace
from agentic_swmm.utils.subprocess_runner import CommandResult


def _result(rc: int, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(command=["x"], return_code=rc, started_at_utc="", finished_at_utc="",
                         stdout=stdout, stderr=stderr)


class TestReviewVerdict:
    def test_a_fail_verdict_is_printed_as_a_verdict(self, tmp_path, monkeypatch, capsys):
        verdict = "Design review: FAIL (3 pass, 2 fail, 1 warn, 5 needs-data)\n  Report: r.md"
        monkeypatch.setattr(review, "run_command", lambda cmd, check=True: _result(1, verdict))
        rc = review.main(Namespace(run_dir=tmp_path, rules=None, out_dir=None))
        out = capsys.readouterr()
        assert rc == 1
        assert "Design review: FAIL" in out.out
        assert "command failed" not in out.out + out.err
        assert "error" not in out.out.lower()

    def test_a_pass_verdict_exits_zero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(review, "run_command", lambda cmd, check=True: _result(0, "Design review: PASS"))
        assert review.main(Namespace(run_dir=tmp_path, rules=None, out_dir=None)) == 0
        assert "Design review: PASS" in capsys.readouterr().out

    def test_a_real_crash_still_surfaces_its_stderr(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(review, "run_command", lambda cmd, check=True: _result(2, "", "Traceback: boom"))
        assert review.main(Namespace(run_dir=tmp_path, rules=None, out_dir=None)) == 2
        assert "boom" in capsys.readouterr().err


class TestCompareSpellings:
    def _parse(self, argv: list[str]) -> Namespace:
        parser = argparse.ArgumentParser()
        compare.register(parser.add_subparsers(dest="verb"))
        return parser.parse_args(["compare", *argv])

    def test_audit_spellings_are_accepted(self):
        ns = self._parse(["--run-dir", "runs/a", "--compare-to", "runs/b"])
        assert ns.run_a == Path("runs/a") and ns.run_b == Path("runs/b")

    def test_original_spellings_still_work(self):
        ns = self._parse(["--run-a", "runs/a", "--run-b", "runs/b"])
        assert ns.run_a == Path("runs/a") and ns.run_b == Path("runs/b")


class TestTraceOnACliRun:
    def test_memory_rows_get_their_decision_point_not_a_question_mark(self):
        assert trace._event_type({"decision_point": "planner_intent_disambiguation"}) == "planner_intent_disambiguation"
        assert trace._event_type({"event": "tool_start"}) == "tool_start"
        assert trace._event_type({}) == "?"

    def test_a_run_without_an_agent_trace_says_so(self, tmp_path, capsys):
        run = tmp_path / "run"
        (run / "_agent").mkdir(parents=True)
        (run / "_agent" / "memory_trace.jsonl").write_text(
            json.dumps({"decision_point": "planner_intent_disambiguation", "timestamp": "2026-09-02T18:43:12Z"}) + "\n",
            encoding="utf-8",
        )
        rc = trace.main(Namespace(run_dir=run, source="both", last=5, tail=False, json=False, quiet=False))
        out = capsys.readouterr()
        assert rc == 0
        assert "no agent_trace.jsonl" in out.err
        assert "command_trace.json" in out.err
        assert "planner_intent_disambiguation" in out.out
        assert " ?" not in out.out


class TestTidySummary:
    @pytest.fixture
    def fifteen_stale_runs(self, monkeypatch):
        report = {
            "moved": [{"name": f"agent-{i}", "to": f"/archive/agent-{i}"} for i in range(15)],
            "kept_audited": [], "kept_recent": [],
        }
        monkeypatch.setattr(runs_tidy, "tidy_agent_runs", lambda root, days, dry_run: report)
        return report

    def test_a_dry_run_is_a_summary(self, fifteen_stale_runs, capsys):
        rc = runs_tidy.main(Namespace(runs_root=None, days=30, dry_run=True, verbose=False))
        lines = capsys.readouterr().out.splitlines()
        assert rc == 0
        assert lines[0].startswith("runs tidy: would archive 15 run(s)")
        assert sum(1 for l in lines if " -> " in l) == 10
        assert any("... and 5 more (pass --verbose to list every run)" in l for l in lines)
        assert lines[-1].startswith("dry run: nothing moved")

    def test_verbose_lists_every_run(self, fifteen_stale_runs, capsys):
        runs_tidy.main(Namespace(runs_root=None, days=30, dry_run=True, verbose=True))
        lines = capsys.readouterr().out.splitlines()
        assert sum(1 for l in lines if " -> " in l) == 15
        assert not any("more (pass --verbose" in l for l in lines)
