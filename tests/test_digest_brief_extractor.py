"""PRD-185: brief-result extractor for digest mode.

The digest renderer collapses each step onto a single line that ends
with a one-line summary of the tool's structured return. e.g.::

    [18] select_skill (read-only, auto)  ok  swmm-experiment-audit
    [19] list_dir (read-only, auto)  ok  8 entries

The extractor in ``agentic_swmm.agent.digest_render`` owns the mapping
``tool_name + result_dict -> brief str``. The PRD requires per-tool
coverage for:

* ``list_dir`` (count of entries)
* ``select_skill`` (skill name)
* ``run_swmm_inp`` (run id or status)
* ``audit_run`` (audit outcome)
* ``inspect_plot_options`` (counts of selectable options)
* ``recall_session_history`` (session count)

Tools without a tailored mapping fall back to ``result["summary"]``
truncated to a single line; if neither is present the brief is empty.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.digest_render import brief_result


class BriefResultExtractorTests(unittest.TestCase):
    def test_list_dir_returns_entry_count(self) -> None:
        result = {
            "tool": "list_dir",
            "ok": True,
            "results": {
                "entries": [
                    "a.py",
                    "b.py",
                    "c.py",
                    "d.py",
                    "e.py",
                    "f.py",
                    "g.py",
                    "h.py",
                ],
            },
            "summary": "listed 8 entries",
        }
        self.assertEqual(brief_result("list_dir", result), "8 entries")

    def test_list_dir_zero_entries_is_explicit(self) -> None:
        result = {"tool": "list_dir", "ok": True, "results": {"entries": []}}
        self.assertEqual(brief_result("list_dir", result), "0 entries")

    def test_list_dir_results_as_flat_list_is_supported(self) -> None:
        # Regression: the live ``_list_dir_tool`` returns ``results``
        # as a flat list (not the ``{"entries": [...]}`` wrapper).
        # The extractor must accept both shapes so digest mode does
        # not crash on the real tool output.
        result = {
            "tool": "list_dir",
            "ok": True,
            "results": [
                {"name": "a.py", "type": "file"},
                {"name": "b.py", "type": "file"},
                {"name": "c.py", "type": "file"},
            ],
            "summary": "3 entries",
        }
        self.assertEqual(brief_result("list_dir", result), "3 entries")

    def test_recall_session_history_flat_list_is_supported(self) -> None:
        # Same shape contract as list_dir — the live tool returns
        # ``results`` as a flat list of session records.
        result = {
            "tool": "recall_session_history",
            "ok": True,
            "results": [{"id": 1}, {"id": 2}],
        }
        self.assertEqual(
            brief_result("recall_session_history", result),
            "2 sessions",
        )

    def test_select_skill_returns_skill_name(self) -> None:
        result = {
            "tool": "select_skill",
            "ok": True,
            "skill_name": "swmm-experiment-audit",
            "tools": [{"name": "audit_run"}],
            "summary": "selected skill swmm-experiment-audit: 1 tool(s) (registry)",
        }
        self.assertEqual(brief_result("select_skill", result), "swmm-experiment-audit")

    def test_run_swmm_inp_returns_run_dir_leaf(self) -> None:
        result = {
            "tool": "run_swmm_inp",
            "ok": True,
            "results": {"runDir": "/abs/path/runs/agent/saanich-1779596754"},
            "summary": "ran swmm-runner.swmm_run",
        }
        self.assertEqual(brief_result("run_swmm_inp", result), "saanich-1779596754")

    def test_run_swmm_inp_falls_back_to_summary_when_no_run_dir(self) -> None:
        result = {"tool": "run_swmm_inp", "ok": True, "summary": "ran swmm-runner.swmm_run"}
        self.assertEqual(brief_result("run_swmm_inp", result), "ran swmm-runner.swmm_run")

    def test_audit_run_returns_status_when_present(self) -> None:
        result = {
            "tool": "audit_run",
            "ok": True,
            "results": {"status": "PASS"},
            "summary": "audited run abc",
        }
        self.assertEqual(brief_result("audit_run", result), "PASS")

    def test_inspect_plot_options_summarises_counts(self) -> None:
        result = {
            "tool": "inspect_plot_options",
            "ok": True,
            "summary": "rain=2 nodes=4 attrs=6",
        }
        self.assertEqual(
            brief_result("inspect_plot_options", result),
            "rain=2 nodes=4 attrs=6",
        )

    def test_recall_session_history_returns_session_count(self) -> None:
        result = {
            "tool": "recall_session_history",
            "ok": True,
            "results": {"sessions": [{"id": 1}, {"id": 2}, {"id": 3}]},
        }
        self.assertEqual(
            brief_result("recall_session_history", result),
            "3 sessions",
        )

    def test_unknown_tool_falls_back_to_summary_first_line(self) -> None:
        result = {
            "tool": "some_new_tool",
            "ok": True,
            "summary": "done\nextra line ignored",
        }
        self.assertEqual(brief_result("some_new_tool", result), "done")

    def test_no_summary_yields_empty_string(self) -> None:
        result = {"tool": "weird_tool", "ok": True}
        self.assertEqual(brief_result("weird_tool", result), "")

    def test_failure_brief_uses_summary(self) -> None:
        # On failure the extractor still returns a meaningful brief so
        # the digest line can carry it next to the ✗ marker.
        result = {
            "tool": "inspect_plot_options",
            "ok": False,
            "summary": "out_file not in repo",
        }
        self.assertEqual(
            brief_result("inspect_plot_options", result),
            "out_file not in repo",
        )


if __name__ == "__main__":
    unittest.main()
