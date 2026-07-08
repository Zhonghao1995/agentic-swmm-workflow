"""ADR-0004 read-side contract for ``run_artifacts.find_inp`` / ``find_out``.

The canonical stage numbers (``05_builder`` / ``06_runner``) are the only
ones any writer produces going forward, but a run directory created before
this migration — or a bare/flat agent-path run that predates any stage
folders at all — must stay readable forever (``run_layout.LEGACY_ALIASES``).
This file locks in both directions: a fresh canonical-layout run resolves,
and an old-layout run (Generation B numbering, or a flat run dir with no
stage folders whatsoever) still resolves via the legacy-fallback chain.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.swmm_runtime.run_artifacts import find_inp, find_out


def test_find_inp_and_find_out_resolve_canonical_layout(tmp_path: Path) -> None:
    """A fresh run dir (``05_builder`` + ``06_runner``) resolves without a manifest."""
    run_dir = tmp_path / "run"
    (run_dir / "05_builder").mkdir(parents=True)
    (run_dir / "06_runner").mkdir(parents=True)
    inp = run_dir / "05_builder" / "model.inp"
    out = run_dir / "06_runner" / "model.out"
    inp.write_text("[TITLE]\nfixture\n", encoding="utf-8")
    out.write_bytes(b"\x00")

    assert find_inp(run_dir, {}) == inp
    assert find_out(run_dir, {}) == out


def test_find_inp_and_find_out_resolve_legacy_generation_b_layout(tmp_path: Path) -> None:
    """An OLD-layout run dir (``04_builder`` + ``05_runner``, pre-ADR-0004)
    must still resolve — this is the read-only tolerance guarantee:
    nothing writes these names again, but they stay findable forever."""
    run_dir = tmp_path / "run"
    (run_dir / "04_builder").mkdir(parents=True)
    (run_dir / "05_runner").mkdir(parents=True)
    inp = run_dir / "04_builder" / "model.inp"
    out = run_dir / "05_runner" / "model.out"
    inp.write_text("[TITLE]\nfixture\n", encoding="utf-8")
    out.write_bytes(b"\x00")

    assert find_inp(run_dir, {}) == inp
    assert find_out(run_dir, {}) == out


def test_find_inp_and_find_out_resolve_flat_legacy_layout(tmp_path: Path) -> None:
    """The oldest agent-path shape: no stage folders at all, everything
    sits flat at the run-dir root. Must still resolve via the bare-glob
    fallback (the pre-stage-folder convention)."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    inp = run_dir / "model.inp"
    out = run_dir / "model.out"
    inp.write_text("[TITLE]\nfixture\n", encoding="utf-8")
    out.write_bytes(b"\x00")

    assert find_inp(run_dir, {}) == inp
    assert find_out(run_dir, {}) == out


def test_find_inp_and_find_out_prefer_manifest_recorded_path(tmp_path: Path) -> None:
    """A manifest-recorded absolute path wins over any glob convention,
    regardless of which generation's stage folder it points into."""
    run_dir = tmp_path / "run"
    (run_dir / "06_runner").mkdir(parents=True)
    recorded_inp = tmp_path / "elsewhere" / "custom.inp"
    recorded_inp.parent.mkdir(parents=True)
    recorded_inp.write_text("[TITLE]\nfixture\n", encoding="utf-8")
    recorded_out = run_dir / "06_runner" / "model.out"
    recorded_out.write_bytes(b"\x00")

    manifest = {"inp": str(recorded_inp), "files": {"out": str(recorded_out)}}
    assert find_inp(run_dir, manifest) == recorded_inp
    assert find_out(run_dir, manifest) == recorded_out
