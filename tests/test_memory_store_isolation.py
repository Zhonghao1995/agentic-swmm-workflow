"""The suite must never write into the project's real memory stores (F-14)."""

from __future__ import annotations

import os
from pathlib import Path

from agentic_swmm.memory.run_failures import resolve_store
from agentic_swmm.utils.paths import repo_root


def test_the_suite_points_memory_at_a_copy() -> None:
    override = os.environ.get("AISWMM_MEMORY_DIR")
    assert override, "conftest must export AISWMM_MEMORY_DIR for the whole session"
    real = (repo_root() / "memory" / "modeling-memory").resolve()
    assert Path(override).resolve() != real


def test_run_failures_go_to_the_copy() -> None:
    store = resolve_store()
    real = (repo_root() / "memory" / "modeling-memory" / "run_failures.jsonl").resolve()
    assert store.resolve() != real


def test_the_shipped_tables_resolve_from_initial_when_the_copy_has_none() -> None:
    # Memory layout 2026-09-06: the copy is an empty store; readers of the
    # shipped tables (benchmarks, citations, storm library) fall back to
    # memory/initial/ through reference_table_path.
    from agentic_swmm.utils.paths import initial_memory_dir, reference_table_path

    override = Path(os.environ["AISWMM_MEMORY_DIR"])
    assert not (override / "reference_benchmarks.yaml").exists()
    for name in ("reference_benchmarks.yaml", "citations.yaml", "storm_library.yaml"):
        resolved = reference_table_path(name, override)
        assert resolved == initial_memory_dir() / name
        assert resolved.is_file()
