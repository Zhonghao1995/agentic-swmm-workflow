"""Repository fixtures are never patched in place; a missing folder is named as such; --help is not a tool (F-151 to F-153).

Live test 2026-09-05, S67: to swap a rain series the planner patched
examples/tecnopolo/tecnopolo_r1_199401.inp in place, ran, and patched it
back; it learned the CLI through six `--help` calls; and list_dir told it a
folder that merely did not exist "must exist inside repository".
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent import permissions, prompts
from agentic_swmm.agent.tool_handlers import runtime_ops
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root

ENVELOPE = """*** Begin Patch
*** Update File: {path}
@@
-[TITLE]
+[TITLE] edited
*** End Patch
"""


def test_examples_and_cases_are_fixtures() -> None:
    assert permissions.is_repository_fixture(repo_root() / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp")
    assert permissions.is_repository_fixture(repo_root() / "cases" / "kelowna" / "model.inp")
    assert not permissions.is_repository_fixture(repo_root() / "runs" / "2026-09-05" / "x_run" / "05_builder" / "model.inp")


def test_a_patch_on_a_fixture_is_refused_with_the_copy_hint(tmp_path: Path) -> None:
    result = AgentToolRegistry().execute(
        ToolCall("apply_patch", {"patch": ENVELOPE.format(path="examples/tecnopolo/tecnopolo_r1_199401.inp")}), tmp_path
    )
    assert result["ok"] is False
    assert "repository fixture" in result["summary"]
    assert "05_builder/model.inp" in result["hint"]
    assert "[TITLE] edited" not in (repo_root() / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp").read_text(encoding="utf-8")


def test_a_missing_folder_is_named_as_missing(tmp_path: Path) -> None:
    result = runtime_ops._list_dir_tool(ToolCall("list_dir", {"path": "runs/2026-09-05/does-not-exist_run/05_builder"}), tmp_path)
    assert result["ok"] is False
    assert "does not exist" in result["summary"]
    assert "inside repository" not in result["summary"]


def test_help_is_not_a_tool() -> None:
    text = Path(prompts.__file__).read_text(encoding="utf-8")
    assert "never run `--help` or `help` through run_allowed_command" in text
    assert "never redo through the CLI what a typed tool has just done" in text
