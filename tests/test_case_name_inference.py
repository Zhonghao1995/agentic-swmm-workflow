"""Five-fixture coverage for :func:`infer_case_name`."""

from __future__ import annotations

import json
from pathlib import Path


def _state(active_run_dir: Path | str | None) -> dict:
    if active_run_dir is None:
        return {"workflow_state": {}}
    return {"workflow_state": {"active_run_dir": str(active_run_dir)}}


def test_provenance_with_case_field_wins(tmp_path: Path) -> None:
    from agentic_swmm.memory.case_inference import infer_case_name

    run_dir = tmp_path / "224244_tecnopolo_run"
    audit = run_dir / "09_audit"
    audit.mkdir(parents=True)
    (audit / "experiment_provenance.json").write_text(
        json.dumps({"case": "tecnopolo"}), encoding="utf-8"
    )
    assert infer_case_name(_state(run_dir)) == "tecnopolo"


def test_provenance_missing_falls_back_to_path_slug(tmp_path: Path) -> None:
    from agentic_swmm.memory.case_inference import infer_case_name

    run_dir = tmp_path / "224244_tecnopolo_run"
    run_dir.mkdir()
    assert infer_case_name(_state(run_dir)) == "tecnopolo"


def test_chat_session_dir_name_is_extracted(tmp_path: Path) -> None:
    from agentic_swmm.memory.case_inference import infer_case_name

    run_dir = tmp_path / "224145_which-demo-you-have_chat"
    run_dir.mkdir()
    assert infer_case_name(_state(run_dir)) == "which-demo-you-have"


def test_malformed_dir_name_returns_none(tmp_path: Path) -> None:
    from agentic_swmm.memory.case_inference import infer_case_name

    run_dir = tmp_path / "not-matching-format"
    run_dir.mkdir()
    assert infer_case_name(_state(run_dir)) is None


def test_no_active_run_dir_returns_none() -> None:
    from agentic_swmm.memory.case_inference import infer_case_name

    assert infer_case_name(_state(None)) is None
    assert infer_case_name({}) is None
    # Counter-fixture: a workflow_state that isn't a dict at all.
    assert infer_case_name({"workflow_state": "garbage"}) is None
