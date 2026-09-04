"""apply_patch never edits an audited run in place.

Live test 2026-09-03 (S50): the planner set allow_evidence_edits itself and
rewrote an archived, audited run's 05_builder/model.inp for a design-storm
rerun, so the run's provenance hashes no longer matched the model on disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from agentic_swmm.agent import permissions
from agentic_swmm.agent.tool_handlers import runtime_ops
from agentic_swmm.agent.types import ToolCall


def _audited_run(root: Path) -> Path:
    run = root / "runs" / "live-test" / "2026-09-03" / "071645_downtown-victoria-bc_run"
    (run / "05_builder").mkdir(parents=True)
    (run / "05_builder" / "model.inp").write_text("[OPTIONS]\nSTART_DATE 11/01/2023\n", encoding="utf-8")
    (run / "09_audit").mkdir()
    (run / "09_audit" / "experiment_provenance.json").write_text(json.dumps({"status": "pass"}), encoding="utf-8")
    return run


def _patch_for(rel: str) -> str:
    return (
        "*** Begin Patch\n"
        f"*** Update File: {rel}\n"
        "@@\n-START_DATE 11/01/2023\n+START_DATE 01/01/2000\n"
        "*** End Patch\n"
    )


def test_audited_run_root_finds_the_owning_run(tmp_path: Path) -> None:
    run = _audited_run(tmp_path)
    assert permissions.audited_run_root(run / "05_builder" / "model.inp") == run
    assert permissions.audited_run_root(tmp_path / "runs" / "live-test" / "other" / "x.inp") is None


def test_apply_patch_refuses_an_audited_run_even_with_the_planner_flag(tmp_path: Path, monkeypatch) -> None:
    run = _audited_run(tmp_path)
    monkeypatch.delenv(permissions.AUDITED_RUN_EDITS_ENV, raising=False)
    rel = str(run.relative_to(tmp_path) / "05_builder" / "model.inp")
    with mock.patch("agentic_swmm.agent.tool_handlers._shared.repo_root", return_value=tmp_path), mock.patch(
        "agentic_swmm.agent.permissions.repo_root", return_value=tmp_path
    ), mock.patch("agentic_swmm.agent.tool_handlers.runtime_ops.repo_root", return_value=tmp_path):
        result = runtime_ops._apply_patch_tool(
            ToolCall(name="apply_patch", args={"patch": _patch_for(rel), "allow_evidence_edits": True}), tmp_path / "session"
        )
    assert result["ok"] is False
    assert "audited run" in result["summary"]
    assert "Copy the model into the current session" in (result.get("hint") or "")
    assert "START_DATE 11/01/2023" in (run / "05_builder" / "model.inp").read_text(encoding="utf-8")


def test_a_human_can_lift_the_guard_with_the_env(tmp_path: Path, monkeypatch) -> None:
    run = _audited_run(tmp_path)
    monkeypatch.setenv(permissions.AUDITED_RUN_EDITS_ENV, "1")
    rel = str(run.relative_to(tmp_path) / "05_builder" / "model.inp")
    with mock.patch("agentic_swmm.agent.tool_handlers._shared.repo_root", return_value=tmp_path), mock.patch(
        "agentic_swmm.agent.permissions.repo_root", return_value=tmp_path
    ), mock.patch("agentic_swmm.agent.tool_handlers.runtime_ops.repo_root", return_value=tmp_path):
        result = runtime_ops._apply_patch_tool(
            ToolCall(name="apply_patch", args={"patch": _patch_for(rel), "allow_evidence_edits": True}), tmp_path / "session"
        )
    assert "audited run" not in str(result.get("summary") or "")


def test_the_approval_detail_names_an_audited_run_edit(tmp_path: Path) -> None:
    run = _audited_run(tmp_path)
    rel = str(run.relative_to(tmp_path) / "05_builder" / "model.inp")
    with mock.patch("agentic_swmm.utils.paths.repo_root", return_value=tmp_path):
        detail = permissions.approval_detail({"patch": _patch_for(rel)})
    assert detail.startswith("EDITS AN AUDITED RUN: writes")
