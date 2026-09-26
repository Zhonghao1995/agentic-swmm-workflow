"""Non-destructive evidence filter: runs listed in excluded_runs.txt must be
dropped from the modeling-memory evidence base (matched by project_key /
case_name / run_id), while everything else is kept.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "swmm-modeling-memory"
    / "scripts"
    / "summarize_memory.py"
)
_spec = importlib.util.spec_from_file_location("summarize_memory_excl_test", _SCRIPT)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)


def test_load_excluded_runs_parses_file_ignoring_comments_and_blanks(tmp_path: Path) -> None:
    (tmp_path / "excluded_runs.txt").write_text(
        "# a comment\nagent-gpt55-demo\n\n  swmm_run  \n# another\n", encoding="utf-8"
    )
    assert sm.load_excluded_runs(tmp_path) == {"agent-gpt55-demo", "swmm_run"}


def test_load_excluded_runs_missing_file_is_empty(tmp_path: Path) -> None:
    assert sm.load_excluded_runs(tmp_path) == set()


def test_is_excluded_matches_any_identifier() -> None:
    excluded = {"agent-gpt55-demo", "swmm_run"}
    assert sm._is_excluded({"project_key": "agent-gpt55-demo", "case_name": "x", "run_id": "y"}, excluded)
    assert sm._is_excluded({"project_key": "p", "case_name": "x", "run_id": "swmm_run"}, excluded)
    # a real case must NOT be excluded
    assert not sm._is_excluded(
        {"project_key": "tecnopolo", "case_name": "tecnopolo", "run_id": "tecnopolo-199401"}, excluded
    )


# The seeded excluded_runs.txt no longer ships (memory layout 2026-09-06:
# nothing program-related under memory/ is tracked); the loader is still
# covered by the tests above.
