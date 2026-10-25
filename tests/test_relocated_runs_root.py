"""A runs root outside the checkout keeps the canonical policy view.

The runs directory can live elsewhere: ``AISWMM_RUNS_ROOT`` points at it,
or ``<repo>/runs`` is a symlink onto another disk (2026-10-25: a 22 GB
runs tree moved to an external SSD). The policy checks in ``permissions``
used to compute every path relative to ``repo_root()`` alone, so a run on
another disk was no longer evidence, apply_patch refused its files and
summaries printed absolute paths. ``paths.checkout_relative`` maps the
relocated root back under ``runs/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent import permissions
from agentic_swmm.agent.swmm_runtime import run_manifests
from agentic_swmm.agent.ui import display_path
from agentic_swmm.utils import paths

RUN = Path("2026-10-25") / "120000_victoria_run"


@pytest.fixture
def ssd_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "ssd" / "runs"
    root.mkdir(parents=True)
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(root))
    monkeypatch.setattr(paths, "_EXTRA_WORKSPACE_ROOTS", [])
    return root


def test_a_relocated_runs_root_maps_back_under_runs(ssd_runs: Path) -> None:
    model = ssd_runs / RUN / "05_builder" / "model.inp"
    assert paths.checkout_relative(model) == Path("runs") / RUN / "05_builder" / "model.inp"


def test_paths_inside_the_checkout_are_unchanged(ssd_runs: Path) -> None:
    assert paths.checkout_relative(paths.repo_root() / "skills" / "x.md") == Path("skills/x.md")


def test_paths_outside_both_roots_are_refused(tmp_path: Path, ssd_runs: Path) -> None:
    elsewhere = tmp_path / "elsewhere" / "model.inp"
    assert paths.checkout_relative(elsewhere) is None
    assert not permissions.is_evidence_path(elsewhere)
    assert not permissions.is_agent_scratch_path(elsewhere)
    assert not permissions.is_allowed_write_path(elsewhere)


def test_a_run_on_another_disk_is_still_evidence(ssd_runs: Path) -> None:
    run = ssd_runs / RUN
    assert permissions.is_evidence_path(run / "05_builder" / "model.inp")
    assert permissions.is_agent_scratch_path(run / "_agent" / "scripts" / "ensemble.mjs")
    assert not permissions.is_agent_scratch_path(run / "_agent" / "agent_trace.jsonl")


def test_apply_patch_policy_allows_writes_under_a_relocated_run(
    ssd_runs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(permissions.REPO_WRITES_ENV, raising=False)
    run = ssd_runs / RUN
    assert permissions.is_allowed_write_path(run / "05_builder" / "model.inp")
    # Agent code under the run's scratch space needs no repo-writes override.
    assert permissions.is_allowed_write_path(run / "_agent" / "scripts" / "ensemble.mjs")
    assert not permissions.is_allowed_write_path(run / ".git" / "config")


def test_the_relocated_runs_root_is_a_workspace_root(ssd_runs: Path) -> None:
    run = ssd_runs / RUN
    run.mkdir(parents=True)
    assert ssd_runs.resolve() in paths.workspace_roots()
    assert paths.resolve_workspace_path(str(run)) == run.resolve()


def test_a_symlinked_runs_directory_resolves_to_the_disk_behind_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "ssd" / "runs"
    real.mkdir(parents=True)
    link = tmp_path / "checkout" / "runs"
    link.parent.mkdir()
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("AISWMM_RUNS_ROOT", str(link))
    monkeypatch.setattr(paths, "_EXTRA_WORKSPACE_ROOTS", [])
    model = link / RUN / "05_builder" / "model.inp"
    model.parent.mkdir(parents=True)
    model.write_text("[TITLE]\n", encoding="utf-8")
    assert paths.checkout_relative(model) == Path("runs") / RUN / "05_builder" / "model.inp"
    assert permissions.is_evidence_path(model)
    assert paths.resolve_workspace_path(str(model)) == model.resolve()


def test_summaries_and_manifests_keep_the_runs_prefix(ssd_runs: Path) -> None:
    model = ssd_runs / RUN / "05_builder" / "model.inp"
    expected = str(Path("runs") / RUN / "05_builder" / "model.inp")
    assert display_path(model) == expected
    assert run_manifests.rel_to_repo(model) == expected
    assert run_manifests.source_type_of(model) == "repository_inp"
