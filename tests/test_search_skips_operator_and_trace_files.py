"""search_files does not roam operator tooling, traces or logs (F-59).

Live finding 2026-09-02 (scenario S10, design storm): the planner searched
the campaign's raw pty transcripts under runs/.../_harness/ and read the
scenario file that had produced its own prompt, on the way to "I need IDF
data". Nothing under an underscore directory is evidence.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers import runtime_ops
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root


def test_underscore_directories_are_operator_or_trace_paths():
    assert runtime_ops._is_operator_or_trace_path(Path("runs/x/_agent/agent_trace.jsonl"))
    assert runtime_ops._is_operator_or_trace_path(Path("runs/live/_harness/transcripts/a.raw.log"))
    assert not runtime_ops._is_operator_or_trace_path(Path("runs/x/06_runner/model.rpt"))
    assert not runtime_ops._is_operator_or_trace_path(Path("_underscore_file.txt"))


def test_logs_and_jsonl_are_never_searched():
    assert ".log" in runtime_ops._SEARCH_SKIP_SUFFIXES
    assert ".jsonl" in runtime_ops._SEARCH_SKIP_SUFFIXES


def test_a_search_over_a_run_skips_its_agent_folder(tmp_path, monkeypatch):
    root = repo_root()
    run = root / "runs" / "f59_tmp_run"
    try:
        (run / "_agent").mkdir(parents=True)
        (run / "06_runner").mkdir()
        (run / "_agent" / "final_report.md").write_text("NEEDLE in the report")
        (run / "06_runner" / "notes.md").write_text("NEEDLE in the run")
        registry = AgentToolRegistry()
        result = registry.execute(
            ToolCall(name="search_files", args={"query": "NEEDLE", "glob": "runs/f59_tmp_run/**/*.md", "max_results": 10}),
            Path("."),
        )
        paths = {r.get("path") or r.get("file") for r in result["results"]}
        assert any("06_runner" in str(p) for p in paths)
        assert not any("_agent" in str(p) for p in paths)
    finally:
        import shutil

        shutil.rmtree(run, ignore_errors=True)
