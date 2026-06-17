"""The modeling-memory proposal pipeline is evidence-gated: a pattern is only
surfaced as a skill proposal once it has recurred across enough distinct runs.
Below the threshold it is merely 'watched'. An unrecognised pattern that clears
the threshold becomes a NEW-skill proposal (via skill-author); the new-skill
block must stay MOC-safe (no single-backtick tokens in its body).
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
_spec = importlib.util.spec_from_file_location("summarize_memory_under_test", _SCRIPT)
sm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sm)

_WHEN = "2026-06-16T00:00:00Z"


def _records(pattern: str, n_runs: int) -> list[dict]:
    """n distinct runs each exhibiting the pattern."""
    return [{"failure_patterns": [pattern], "run_id": f"run-{i}"} for i in range(n_runs)]


def test_pattern_below_threshold_is_watched_not_proposed() -> None:
    out = sm.render_proposals(_records("peak_flow_parse_missing", sm._MIN_EVIDENCE_RUNS - 1), _WHEN)
    assert "## Watching (not enough evidence yet)" in out
    assert "peak_flow_parse_missing" in out
    # not yet a proposal: no refinement block and no new-skill block
    assert "Relevant workflow skill(s)" not in out
    assert "candidate for a NEW skill" not in out


def test_known_pattern_at_threshold_is_proposed_as_refinement() -> None:
    out = sm.render_proposals(_records("peak_flow_parse_missing", sm._MIN_EVIDENCE_RUNS), _WHEN)
    block = out.split("## `peak_flow_parse_missing`", 1)[1]
    assert "Relevant workflow skill(s)" in block
    assert "swmm-runner" in block
    assert "candidate for a NEW skill" not in block


def test_unrecognised_pattern_at_threshold_proposes_new_skill() -> None:
    out = sm.render_proposals(_records("totally_novel_pattern", sm._MIN_EVIDENCE_RUNS), _WHEN)
    assert "## `totally_novel_pattern`" in out
    assert "candidate for a NEW skill" in out
    assert "skill-author" in out
    assert "swmm-totally-novel-pattern" in out


def test_unrecognised_pattern_below_threshold_is_only_watched() -> None:
    out = sm.render_proposals(_records("totally_novel_pattern", 1), _WHEN)
    assert "candidate for a NEW skill" not in out
    assert "## Watching (not enough evidence yet)" in out
    assert "totally_novel_pattern" in out


def test_new_skill_block_is_moc_safe() -> None:
    block = sm.render_new_skill_proposal("some_unknown_pattern", ["run-a", "run-b"])
    heading, _, body = block.partition("\n")
    assert "`some_unknown_pattern`" in heading  # heading may be backticked (MOC group 1)
    assert "`" not in body  # ...but the body must be backtick-free
    assert "run-a, run-b" in body
