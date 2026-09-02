"""Calibration file arguments cross the MCP boundary as absolute paths, and a
skill-script crash is reported as its error line (S05, 2026-09-02).

The first swmm_calibrate_search call of the session failed with a twelve-line
pathlib traceback ending in FileNotFoundError for
``examples/calibration/patch_map.json``: the Node server resolves relative
paths against ``mcp/swmm-calibration/``. The planner had to read the stack to
learn to retry with absolute paths.
"""

from __future__ import annotations

from agentic_swmm.agent.tool_handlers import swmm_calibration
from agentic_swmm.agent.tool_handlers._shared import _trim_traceback
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root


class TestAbsolutePaths:
    def test_relative_example_paths_become_absolute_repo_paths(self, tmp_path):
        call = ToolCall("swmm_calibrate_search", {
            "base_inp": "examples/todcreek/model_chicago5min.inp",
            "patch_map": "examples/calibration/patch_map.json",
            "observed": "examples/calibration/observed_flow.csv",
            "run_root": "runs/_cal", "summary_json": "runs/_cal/summary.json",
            "search_space": "examples/calibration/search_space.json",
            "iterations": 8,
        })
        mapped = swmm_calibration._calibrate_search_args(call, tmp_path)
        assert mapped["patchMap"] == str((repo_root() / "examples/calibration/patch_map.json").resolve())
        assert mapped["observed"].startswith(str(repo_root().resolve()))
        assert mapped["searchSpace"].endswith("examples/calibration/search_space.json")
        assert mapped["runRoot"].startswith(str(repo_root().resolve()))
        assert mapped["iterations"] == 8

    def test_absolute_paths_pass_through(self, tmp_path):
        assert swmm_calibration._absolute_repo_path("/elsewhere/obs.csv") == "/elsewhere/obs.csv"


class TestTracebackTrim:
    def test_only_the_error_line_survives(self):
        raw = (
            "python exited 1\nTraceback (most recent call last):\n"
            "  File \"/x/swmm_calibrate.py\", line 1633, in <module>\n    main()\n"
            "  File \"/opt/python3.11/pathlib.py\", line 1044, in open\n"
            "    return io.open(self, mode, buffering, encoding, errors, newline)\n"
            "FileNotFoundError: [Errno 2] No such file or directory: 'examples/calibration/patch_map.json'\n"
        )
        trimmed = _trim_traceback(raw)
        assert trimmed.startswith("python exited 1 FileNotFoundError: [Errno 2]")
        assert "pathlib.py" not in trimmed and "main()" not in trimmed
        assert trimmed.endswith("(traceback trimmed)")

    def test_messages_without_a_traceback_are_untouched(self):
        assert _trim_traceback("MCP transport failed: timeout") == "MCP transport failed: timeout"
