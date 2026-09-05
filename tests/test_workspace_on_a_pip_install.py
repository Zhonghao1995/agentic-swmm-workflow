"""The path sandbox is anchored on the workspace, not on site-packages (F-135, F-137).

Live test 2026-09-04, S59 on the released 0.9.4 wheel: after two fetches
timed out, list_dir on the session's own run directory answered "directory
must exist inside repository". On a pip install repo_root() is
site-packages, so every path-sandboxed tool refused the user's own runs,
and the default runs root sat inside site-packages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils import paths


@pytest.fixture
def wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    site = tmp_path / "site-packages"
    site.mkdir()
    packaged = tmp_path / "aiswmm"
    (packaged / "skills" / "swmm-runner").mkdir(parents=True)
    (packaged / "agent" / "memory").mkdir(parents=True)
    (packaged / "examples" / "tecnopolo").mkdir(parents=True)
    (packaged / "examples" / "tecnopolo" / "model.inp").write_text("[TITLE]\n", encoding="utf-8")
    work = tmp_path / "work"
    (work / "runs" / "2026-09-04" / "231138_downtown-victoria-bc_run" / "06_runner").mkdir(parents=True)
    # On a wheel every module's repo_root() is site-packages; patch the
    # handlers' own names too, the way the existing sandbox tests do.
    from agentic_swmm.agent import permissions
    from agentic_swmm.agent.tool_handlers import _shared, runtime_ops

    for module in (paths, permissions, _shared, runtime_ops):
        monkeypatch.setattr(module, "repo_root", lambda: site)
    monkeypatch.setattr(paths, "packaged_resource_root", lambda: packaged)
    monkeypatch.delenv("AISWMM_RUNS_ROOT", raising=False)
    monkeypatch.chdir(work)
    monkeypatch.setattr(paths, "_EXTRA_WORKSPACE_ROOTS", [])
    return {"site": site, "packaged": packaged, "work": work}


def test_the_default_runs_root_on_a_wheel_is_next_to_the_user(wheel: dict[str, Path]) -> None:
    assert not paths.is_checkout()
    assert paths.resolve_runs_dir() == wheel["work"].resolve() / "runs"


def test_a_checkout_keeps_its_runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    checkout = tmp_path / "checkout"
    (checkout / "skills").mkdir(parents=True)
    (checkout / "agent" / "memory").mkdir(parents=True)
    monkeypatch.setattr(paths, "repo_root", lambda: checkout)
    monkeypatch.delenv("AISWMM_RUNS_ROOT", raising=False)
    assert paths.is_checkout()
    assert paths.resolve_runs_dir() == checkout / "runs"
    assert paths.workspace_roots()[0] == checkout.resolve()


def test_the_users_run_directory_is_listable(wheel: dict[str, Path]) -> None:
    from agentic_swmm.agent.tool_handlers import runtime_ops

    run = wheel["work"] / "runs" / "2026-09-04" / "231138_downtown-victoria-bc_run"
    absolute = runtime_ops._list_dir_tool(ToolCall(name="list_dir", args={"path": str(run)}), run)
    relative = runtime_ops._list_dir_tool(ToolCall(name="list_dir", args={"path": "runs/2026-09-04/231138_downtown-victoria-bc_run"}), run)
    assert absolute["ok"] is True, absolute
    assert relative["ok"] is True, relative


def test_a_registered_session_directory_elsewhere_is_addressable(wheel: dict[str, Path], tmp_path: Path) -> None:
    from agentic_swmm.agent.tool_handlers import runtime_ops

    elsewhere = tmp_path / "somewhere" / "live-test"
    (elsewhere / "2026-09-04" / "x_run").mkdir(parents=True)
    assert runtime_ops._list_dir_tool(ToolCall(name="list_dir", args={"path": str(elsewhere)}), elsewhere)["ok"] is False
    paths.register_workspace_root(elsewhere)
    assert runtime_ops._list_dir_tool(ToolCall(name="list_dir", args={"path": str(elsewhere)}), elsewhere)["ok"] is True


def test_packaged_examples_resolve_and_the_rest_of_the_disk_does_not(wheel: dict[str, Path]) -> None:
    example = paths.resolve_workspace_path("examples/tecnopolo/model.inp")
    assert example == (wheel["packaged"] / "examples" / "tecnopolo" / "model.inp").resolve()
    assert paths.resolve_workspace_path("/etc/hosts") is None
