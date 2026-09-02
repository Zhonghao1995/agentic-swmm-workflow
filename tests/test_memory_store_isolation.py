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


def test_the_copy_carries_the_shipped_stores() -> None:
    # Readers of the shipped stores (benchmarks, citations, storm library)
    # must see the same files they would see in the project.
    override = Path(os.environ["AISWMM_MEMORY_DIR"])
    assert (override / "reference_benchmarks.yaml").is_file()
    assert (override / "citations.yaml").is_file()
