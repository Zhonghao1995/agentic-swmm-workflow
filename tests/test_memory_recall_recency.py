"""Tests for the recency weighting knob in ``recall_search`` (P0-2).

Coverage:
- Knob at 0 (disabled): ranking and scores byte-identical to pre-knob
  behaviour (lock-in test with fixed now_fn).
- Knob enabled: older entries sink below fresher entries of equal base
  relevance (fixed now_fn).
- Missing timestamps: entries without any timestamp field are not
  penalised (age treated as 0 → weight = 1.0).
- ``_age_days_for_entry`` picks timestamp fields in priority order.
- ``_apply_recency_weight`` is a no-op when half_life_days <= 0.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_posix() -> float:
    """Fixed 'now' for deterministic tests: 2026-06-10T00:00:00Z."""
    return datetime(2026, 6, 10, 0, 0, 0, tzinfo=timezone.utc).timestamp()


def _entry(
    run_id: str,
    score: float,
    *,
    last_seen_utc: str | None = None,
    recorded_utc: str | None = None,
    created_utc: str | None = None,
) -> dict[str, Any]:
    e: dict[str, Any] = {"run_id": run_id, "score": score}
    if last_seen_utc is not None:
        e["last_seen_utc"] = last_seen_utc
    if recorded_utc is not None:
        e["recorded_utc"] = recorded_utc
    if created_utc is not None:
        e["created_utc"] = created_utc
    return e


# ---------------------------------------------------------------------------
# _age_days_for_entry
# ---------------------------------------------------------------------------


def test_age_picks_last_seen_utc_first() -> None:
    from agentic_swmm.memory.recall_search import _age_days_for_entry

    now = _now_posix()
    entry = _entry(
        "x",
        1.0,
        last_seen_utc="2026-06-09T00:00:00Z",  # 1 day ago
        recorded_utc="2026-01-01T00:00:00Z",    # much older
    )
    age = _age_days_for_entry(entry, now)
    assert age is not None
    assert abs(age - 1.0) < 0.01


def test_age_falls_back_to_recorded_utc() -> None:
    from agentic_swmm.memory.recall_search import _age_days_for_entry

    now = _now_posix()
    entry = _entry(
        "x",
        1.0,
        recorded_utc="2026-06-07T00:00:00Z",  # 3 days ago
    )
    age = _age_days_for_entry(entry, now)
    assert age is not None
    assert abs(age - 3.0) < 0.01


def test_age_falls_back_to_created_utc() -> None:
    from agentic_swmm.memory.recall_search import _age_days_for_entry

    now = _now_posix()
    entry = _entry(
        "x",
        1.0,
        created_utc="2026-06-05T00:00:00Z",  # 5 days ago
    )
    age = _age_days_for_entry(entry, now)
    assert age is not None
    assert abs(age - 5.0) < 0.01


def test_age_returns_none_when_no_timestamp_present() -> None:
    from agentic_swmm.memory.recall_search import _age_days_for_entry

    entry = {"run_id": "x", "score": 1.0}
    age = _age_days_for_entry(entry, _now_posix())
    assert age is None


def test_age_returns_none_for_unparseable_timestamp() -> None:
    from agentic_swmm.memory.recall_search import _age_days_for_entry

    entry = {"run_id": "x", "score": 1.0, "last_seen_utc": "not-a-date"}
    age = _age_days_for_entry(entry, _now_posix())
    assert age is None


# ---------------------------------------------------------------------------
# _apply_recency_weight: no-op when disabled
# ---------------------------------------------------------------------------


def test_apply_recency_weight_noop_when_half_life_zero() -> None:
    """half_life_days=0 must be a byte-identical no-op."""
    from agentic_swmm.memory.recall_search import _apply_recency_weight

    entries = [
        _entry("a", 1.0, last_seen_utc="2020-01-01T00:00:00Z"),
        _entry("b", 0.8, last_seen_utc="2026-06-09T00:00:00Z"),
    ]
    import copy
    original = copy.deepcopy(entries)
    result = _apply_recency_weight(entries, half_life_days=0, now_fn=_now_posix)

    # Same list object returned.
    assert result is entries
    # Scores unchanged.
    for before, after in zip(original, result):
        assert before["score"] == after["score"]
    # Order unchanged.
    assert [e["run_id"] for e in result] == ["a", "b"]


def test_apply_recency_weight_noop_when_half_life_negative() -> None:
    from agentic_swmm.memory.recall_search import _apply_recency_weight

    entries = [_entry("a", 0.5, last_seen_utc="2020-01-01T00:00:00Z")]
    original_score = entries[0]["score"]
    result = _apply_recency_weight(entries, half_life_days=-5, now_fn=_now_posix)
    assert result[0]["score"] == original_score


# ---------------------------------------------------------------------------
# _apply_recency_weight: older entries sink
# ---------------------------------------------------------------------------


def test_apply_recency_weight_older_entry_sinks() -> None:
    """With a 30-day half-life, an old entry (365 days) scores far lower than a fresh one."""
    from agentic_swmm.memory.recall_search import _apply_recency_weight

    # Both entries have equal base score; the old one should rank lower after weighting.
    old = _entry("old", 1.0, last_seen_utc="2025-06-10T00:00:00Z")  # 365 days ago
    fresh = _entry("fresh", 1.0, last_seen_utc="2026-06-09T00:00:00Z")  # 1 day ago

    result = _apply_recency_weight(
        [old, fresh], half_life_days=30.0, now_fn=_now_posix
    )

    # Fresh entry should rank first after weighting.
    assert result[0]["run_id"] == "fresh"
    assert result[0]["score"] > result[1]["score"]


def test_apply_recency_weight_score_formula_correct() -> None:
    """Score adjustment: 0.5 ** (age_days / half_life)."""
    from agentic_swmm.memory.recall_search import _apply_recency_weight

    # Entry that is exactly half_life_days old → weight should be 0.5.
    entry = _entry("e", 1.0, last_seen_utc="2026-05-11T00:00:00Z")  # 30 days before 2026-06-10
    result = _apply_recency_weight([entry], half_life_days=30.0, now_fn=_now_posix)
    expected = 0.5 ** (30.0 / 30.0)
    assert abs(result[0]["score"] - expected) < 0.001


def test_apply_recency_weight_no_timestamp_not_penalised() -> None:
    """An entry without any timestamp is left at its original score."""
    from agentic_swmm.memory.recall_search import _apply_recency_weight

    no_ts = _entry("no_ts", 0.5)
    old = _entry("old", 0.5, last_seen_utc="2020-01-01T00:00:00Z")
    result = _apply_recency_weight([no_ts, old], half_life_days=30.0, now_fn=_now_posix)

    no_ts_score = next(e["score"] for e in result if e["run_id"] == "no_ts")
    old_score = next(e["score"] for e in result if e["run_id"] == "old")
    assert no_ts_score == 0.5  # unchanged
    assert old_score < no_ts_score  # old entry was penalised


# ---------------------------------------------------------------------------
# Integration: recall_search with half_life_days kwarg
# ---------------------------------------------------------------------------


def test_recall_search_half_life_zero_scores_identical_to_no_knob(tmp_path) -> None:
    """half_life_days=0 in recall_search produces byte-identical scores."""
    import sys

    rag_scripts = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "skills"
        / "swmm-rag-memory"
        / "scripts"
    )
    sys.path.insert(0, str(rag_scripts))
    try:
        import rag_memory_lib as lib
    except ImportError:
        pytest.skip("rag_memory_lib not available")

    # Build a minimal corpus.
    now_iso = "2026-06-09T00:00:00Z"
    entries = [
        {
            "schema_version": "1.1",
            "source_type": "run_record",
            "source_path": "memory/modeling-memory/foo.json",
            "run_id": "r1",
            "case_name": "Case R1",
            "project_key": "p",
            "workflow_mode": "prepared_inp_cli",
            "qa_status": "pass",
            "failure_patterns": [],
            "model_diagnostic_ids": [],
            "next_run_cautions": [],
            "text": "peak flow continuity mass balance",
            "tokens": sorted(set(lib.tokenize("peak flow continuity mass balance"))),
            "last_seen_utc": now_iso,
        },
    ]
    index_dir = tmp_path / "rag"
    lib.write_corpus(entries, index_dir)
    corpus_path = index_dir / "corpus.jsonl"
    lessons_path = tmp_path / "lessons.md"
    lessons_path.write_text("<!-- schema_version: 1.1 -->\n# Lessons\n", encoding="utf-8")

    from agentic_swmm.memory.recall_search import recall_search

    fixed_now = _now_posix()
    results_no_knob = recall_search(
        "peak flow",
        top_k=3,
        index_dir=index_dir,
        corpus_path=corpus_path,
        lessons_path=lessons_path,
        half_life_days=0,
        now_fn=lambda: fixed_now,
    )
    # Run a second time without passing half_life_days at all (default=0).
    results_default = recall_search(
        "peak flow",
        top_k=3,
        index_dir=index_dir,
        corpus_path=corpus_path,
        lessons_path=lessons_path,
    )
    assert len(results_no_knob) == len(results_default)
    for a, b in zip(results_no_knob, results_default):
        assert a["run_id"] == b["run_id"]
        assert a["score"] == b["score"]


def test_recall_search_with_half_life_older_entries_demoted(tmp_path) -> None:
    """With half_life_days set, an older entry of equal base relevance ranks lower."""
    import sys

    rag_scripts = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "skills"
        / "swmm-rag-memory"
        / "scripts"
    )
    sys.path.insert(0, str(rag_scripts))
    try:
        import rag_memory_lib as lib
    except ImportError:
        pytest.skip("rag_memory_lib not available")

    shared_text = "continuity error mass balance check"
    entries = [
        {
            "schema_version": "1.1",
            "source_type": "run_record",
            "source_path": "memory/modeling-memory/a.json",
            "run_id": "fresh",
            "case_name": "Case Fresh",
            "project_key": "p",
            "workflow_mode": "prepared_inp_cli",
            "qa_status": "pass",
            "failure_patterns": [],
            "model_diagnostic_ids": [],
            "next_run_cautions": [],
            "text": shared_text,
            "tokens": sorted(set(lib.tokenize(shared_text))),
            "last_seen_utc": "2026-06-09T00:00:00Z",  # 1 day before fixed "now"
        },
        {
            "schema_version": "1.1",
            "source_type": "run_record",
            "source_path": "memory/modeling-memory/b.json",
            "run_id": "old",
            "case_name": "Case Old",
            "project_key": "p",
            "workflow_mode": "prepared_inp_cli",
            "qa_status": "pass",
            "failure_patterns": [],
            "model_diagnostic_ids": [],
            "next_run_cautions": [],
            "text": shared_text,
            "tokens": sorted(set(lib.tokenize(shared_text))),
            "last_seen_utc": "2025-06-10T00:00:00Z",  # 365 days before fixed "now"
        },
    ]
    index_dir = tmp_path / "rag"
    lib.write_corpus(entries, index_dir)
    corpus_path = index_dir / "corpus.jsonl"
    lessons_path = tmp_path / "lessons.md"
    lessons_path.write_text("<!-- schema_version: 1.1 -->\n# Lessons\n", encoding="utf-8")

    from agentic_swmm.memory.recall_search import recall_search

    results = recall_search(
        shared_text,
        top_k=2,
        index_dir=index_dir,
        corpus_path=corpus_path,
        lessons_path=lessons_path,
        half_life_days=30.0,
        now_fn=_now_posix,
    )

    assert len(results) == 2
    run_ids = [r["run_id"] for r in results]
    # Fresh entry should outrank old after recency weighting.
    assert run_ids[0] == "fresh", f"expected fresh first, got {run_ids}"
    assert results[0]["score"] > results[1]["score"]
