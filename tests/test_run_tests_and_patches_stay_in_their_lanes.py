"""run_tests runs only the repository's tests; a patch never mints a run folder (F-147, F-142).

Live test 2026-09-04, S63 ("the pipes are in feet not meters, redo it"): the
planner wrote a converter under a run folder of its own making,
runs/2026-09-05/victoria-pipe-units-corrected/_agent/scripts/, wrapped it in
runs/.../_agent/test_convert_pipe_units.py and executed it through
run_tests, routing around the product-tree guard, the audited-run guard and
the command allowlist that had each refused it.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest import mock

import pytest

from agentic_swmm.agent import permissions, tool_registry
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root

ENVELOPE = """*** Begin Patch
*** Add File: {path}
+print("hello")
*** End Patch
"""


@pytest.fixture
def existing_run():
    run = repo_root() / "runs" / "2026-09-05" / "010101_f142_existing_run"
    (run / "_agent" / "scripts").mkdir(parents=True, exist_ok=True)
    try:
        yield run
    finally:
        shutil.rmtree(run.parent / "010101_f142_existing_run", ignore_errors=True)


def test_a_patch_into_a_run_folder_that_does_not_exist_is_refused(existing_run):
    minted = repo_root() / "runs" / "2026-09-05" / "victoria-pipe-units-corrected" / "_agent" / "scripts" / "convert.py"
    assert permissions.is_write_into_a_new_run_folder(minted)
    result = AgentToolRegistry().execute(
        ToolCall("apply_patch", {"patch": ENVELOPE.format(path=str(minted.relative_to(repo_root())))}), existing_run
    )
    assert result["ok"] is False
    assert "creates a run folder" in result["summary"]
    assert "never creates a run folder" in result["hint"]
    assert not minted.parent.exists()


def test_a_patch_into_an_existing_runs_scratch_dir_is_still_allowed(existing_run):
    target = existing_run / "_agent" / "scripts" / "helper.py"
    assert not permissions.is_write_into_a_new_run_folder(target)
    result = AgentToolRegistry().execute(
        ToolCall("apply_patch", {"patch": ENVELOPE.format(path=str(target.relative_to(repo_root())))}), existing_run
    )
    assert result["ok"] is True, result


def test_paths_outside_runs_are_not_this_rule():
    assert not permissions.is_write_into_a_new_run_folder(repo_root() / "README.md")
    assert not permissions.is_write_into_a_new_run_folder(repo_root() / "runs" / "README.md")


def test_run_tests_refuses_a_file_under_runs(tmp_path: Path):
    call = ToolCall("run_tests", {"paths": ["runs/2026-09-05/x_run/_agent/test_convert.py"]})
    result = tool_registry._run_tests_tool(call, tmp_path)
    assert result["ok"] is False
    assert "outside the repository's tests/ tree" in result["summary"]
    assert "does not execute agent-written files" in result["hint"]


def test_run_tests_accepts_the_repository_suite(tmp_path: Path):
    with mock.patch.object(tool_registry, "_run_process_tool", return_value={"ok": True, "summary": "ran"}) as run:
        result = tool_registry._run_tests_tool(ToolCall("run_tests", {"paths": ["tests/test_run_tests_and_patches_stay_in_their_lanes.py"]}), tmp_path)
    assert result["ok"] is True
    assert run.call_args.args[2][:3] == [tool_registry.sys.executable, "-m", "pytest"]


def test_run_tests_never_runs_a_bare_python_file(tmp_path: Path):
    with mock.patch.object(tool_registry.importlib.util, "find_spec", return_value=None):
        result = tool_registry._run_tests_tool(ToolCall("run_tests", {"paths": ["tests/test_x.py"]}), tmp_path)
    assert result["ok"] is False
    assert "pytest is not installed" in result["summary"]
