"""Direct tests for the audit-hook phase pipeline (audit_hook.py).

The end-to-end behaviour is covered by the six audit-hook bridge test
files (which passed unchanged across the pipeline refactor — that is
the interface-stability proof). This file pins the pipeline mechanics
themselves: phase order is behaviour, each phase is fail-soft in
isolation, and phases are directly invokable with a bare context.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

from agentic_swmm.memory import audit_hook


def _ctx(tmp_path: Path) -> audit_hook._RefreshContext:
    return audit_hook._RefreshContext(
        run_dir=tmp_path / "run",
        runs_dir=tmp_path / "runs",
        project_root=tmp_path,
        memory_dir=tmp_path / "memory",
        lessons_path=tmp_path / "memory" / "lessons_learned.md",
        result={"skipped": False, "reason": "", "errors": []},
    )


def test_phase_order_is_pinned() -> None:
    """Order is behaviour: lifecycle bump before decay (ME-2 reads the
    bumped metadata); parametric before negative-lessons (eligibility
    marker) and before the outcome ledger (complete provenance)."""
    names = [phase.__name__ for phase in audit_hook._REFRESH_PHASES]
    assert names == [
        "_phase_compaction_marker",
        "_phase_memory_moc",
        "_phase_lifecycle_metadata",
        "_phase_parametric_bridge",
        "_phase_calibration_bridge",
        "_phase_negative_lessons",
        "_phase_decay_pass",
        "_phase_outcome_ledger",
    ]


def test_negative_lessons_phase_gates_on_parametric_marker(tmp_path: Path) -> None:
    """No parametric_memory key → the phase is a strict no-op (never
    calls the bridge, never appends an error)."""
    ctx = _ctx(tmp_path)
    with mock.patch.object(
        audit_hook, "_record_negative_lesson_for_continuity_fail"
    ) as bridge:
        audit_hook._phase_negative_lessons(ctx)
    bridge.assert_not_called()
    assert ctx.result["errors"] == []
    assert "negative_lessons" not in ctx.result


def test_each_phase_is_fail_soft_in_isolation(tmp_path: Path) -> None:
    """A phase whose underlying bridge raises appends one error string
    and returns — it never propagates. Exercised on the parametric
    phase (the one with a nested inner try)."""
    ctx = _ctx(tmp_path)
    with mock.patch.object(
        audit_hook,
        "_record_parametric_from_provenance",
        side_effect=RuntimeError("boom"),
    ):
        audit_hook._phase_parametric_bridge(ctx)  # must not raise
    assert any("parametric memory write failed" in e for e in ctx.result["errors"])


def test_one_broken_phase_does_not_block_the_next(tmp_path: Path) -> None:
    """Sequencer semantics: run two phases where the first raises
    internally; the second still executes and records its key."""
    ctx = _ctx(tmp_path)
    with mock.patch.object(
        audit_hook,
        "_record_parametric_from_provenance",
        side_effect=RuntimeError("boom"),
    ), mock.patch.object(
        audit_hook,
        "_record_calibration_from_provenance",
        return_value=str(tmp_path / "memory" / "calibration_memory.jsonl"),
    ):
        for phase in (
            audit_hook._phase_parametric_bridge,
            audit_hook._phase_calibration_bridge,
        ):
            phase(ctx)
    assert "calibration_memory" in ctx.result
    assert any("parametric" in e for e in ctx.result["errors"])
