"""Tests for the new ``final_report.md`` template.

PRD_runtime "Module: Report template" + Done Criteria
``test_session_writes_new_report_template``.

- Contains ``## What I did``.
- Contains ``## What you got``.
- Does NOT contain the inline ``allowed_tools`` comma list (a footer
  reference to ``agent_trace.jsonl`` is allowed).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentic_swmm.agent.reporting import write_report


def _call(name: str, args: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, args=args or {})


class WriteReportTemplateTests(unittest.TestCase):
    def test_report_has_what_i_did_and_what_you_got_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            plan = [
                _call("inspect_plot_options", {"run_dir": "runs/agent/x"}),
                _call("plot_run", {"run_dir": "runs/agent/x", "node": "J2"}),
            ]
            results = [
                {"tool": "inspect_plot_options", "ok": True, "summary": "rain=1 nodes=3"},
                {
                    "tool": "plot_run",
                    "ok": True,
                    "summary": "plot saved",
                    "path": "runs/agent/x/07_plots/fig.png",
                },
            ]
            path = write_report(
                session_dir,
                goal="plot J2 depth",
                plan=plan,
                results=results,
                dry_run=False,
                allowed_tools={"plot_run", "inspect_plot_options", "read_file"},
                planner="openai",
                final_text="done",
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("## What I did", text)
        self.assertIn("## What you got", text)

    def test_report_does_not_inline_allowed_tools_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            allowed = {"plot_run", "inspect_plot_options", "read_file", "list_skills"}
            path = write_report(
                session_dir,
                goal="hi",
                plan=[_call("read_file", {"path": "README.md"})],
                results=[{"tool": "read_file", "ok": True, "summary": "ok"}],
                dry_run=False,
                allowed_tools=allowed,
                planner="openai",
            )
            text = path.read_text(encoding="utf-8")
        # The comma-separated dump must be gone.
        comma_list = ", ".join(sorted(allowed))
        self.assertNotIn(comma_list, text, "inline allowed_tools list must be dropped")
        # A footer reference to agent_trace.jsonl is fine and expected.
        self.assertIn("agent_trace.jsonl", text)


if __name__ == "__main__":
    unittest.main()
