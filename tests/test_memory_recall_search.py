"""Unit tests for ``agentic_swmm.memory.recall_search`` (PRD M6).

These tests are pure: they construct a tiny fixture corpus + indexes
(no embedding model call, no LLM), and feed them to ``recall_search``.
The 761-LOC ``rag_memory_lib.py`` is exercised indirectly but never
re-implemented.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


def _build_fixture_corpus(tmp_path: Path) -> tuple[Path, Path]:
    """Write a 3-entry corpus + matching indexes; return (index_dir, lessons_path)."""
    # We re-use the same rag_memory_lib.write_corpus helper so the
    # indexes match what production builds: that exercises the real
    # tokenizer + hashed embedding.
    import sys

    rag_scripts = (
        Path(__file__).resolve().parents[1] / "skills" / "swmm-rag-memory" / "scripts"
    )
    sys.path.insert(0, str(rag_scripts))
    try:
        import rag_memory_lib as lib
    finally:
        # leave it on sys.path so the production wrapper can import too.
        pass

    repo_root = tmp_path
    entries = [
        {
            "schema_version": "1.1",
            "source_type": "run_record",
            "source_path": "memory/modeling-memory/modeling_memory_index.json",
            "run_id": "case-a",
            "case_name": "Case A",
            "project_key": "p1",
            "workflow_mode": "prepared_inp_cli",
            "qa_status": "pass",
            "failure_patterns": ["peak_flow_parse_missing"],
            "model_diagnostic_ids": [],
            "next_run_cautions": [],
            "text": "peak flow parse missing peak_flow_parse_missing rpt outfall",
            "tokens": sorted(set(lib.tokenize("peak flow parse missing peak_flow_parse_missing rpt outfall"))),
        },
        {
            "schema_version": "1.1",
            "source_type": "experiment_note",
            "source_path": "runs/case-b/09_audit/experiment_note.md",
            "run_id": "case-b",
            "case_name": "Case B",
            "project_key": "p1",
            "workflow_mode": "prepared_inp_cli",
            "qa_status": "pass",
            "failure_patterns": ["continuity_parse_missing"],
            "model_diagnostic_ids": [],
            "next_run_cautions": [],
            "text": "continuity parse missing continuity_error 连续性 水量平衡",
            "tokens": sorted(set(lib.tokenize("continuity parse missing continuity_error 连续性 水量平衡"))),
        },
        {
            "schema_version": "1.1",
            "source_type": "chat_note",
            "source_path": "runs/2026-05-13/session-1/chat_note.md",
            "run_id": "chat-1",
            "case_name": "Chat-1",
            "project_key": "p1",
            "workflow_mode": "chat",
            "qa_status": "n/a",
            "failure_patterns": [],
            "model_diagnostic_ids": [],
            "next_run_cautions": [],
            "text": "discussion of how to set raingage id and timeseries",
            "tokens": sorted(set(lib.tokenize("discussion of how to set raingage id and timeseries"))),
        },
    ]

    index_dir = repo_root / "memory" / "rag-memory"
    lib.write_corpus(entries, index_dir)

    lessons_path = repo_root / "memory" / "modeling-memory" / "lessons_learned.md"
    lessons_path.parent.mkdir(parents=True, exist_ok=True)
    lessons_path.write_text(
        "<!-- schema_version: 1.1 -->\n# Lessons\n\n## peak_flow_parse_missing\n\nbody.\n",
        encoding="utf-8",
    )
    return index_dir, lessons_path


def test_recall_search_returns_top_k_for_an_exact_token_query(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall_search import recall_search

    index_dir, lessons_path = _build_fixture_corpus(tmp_path)
    corpus_path = index_dir / "corpus.jsonl"
    results = recall_search(
        "peak flow",
        top_k=3,
        index_dir=index_dir,
        corpus_path=corpus_path,
        lessons_path=lessons_path,
    )
    assert results, "expected at least one hit for an exact-token query"
    top = results[0]
    assert top["run_id"] == "case-a"
    assert top.get("case_name") == "Case A"
    assert top.get("score", 0) > 0
    # Every entry must surface the contract fields we register.
    for entry in results:
        assert entry.get("case_name")
        assert entry.get("run_id")
        assert "schema_version" in entry


def test_recall_search_chinese_query_routes_via_expansion(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall_search import recall_search

    index_dir, lessons_path = _build_fixture_corpus(tmp_path)
    corpus_path = index_dir / "corpus.jsonl"
    results = recall_search(
        "为什么连续性误差又出现了",
        top_k=3,
        index_dir=index_dir,
        corpus_path=corpus_path,
        lessons_path=lessons_path,
    )
    assert results, "expected Chinese query to hit via expansion table"
    run_ids = {r.get("run_id") for r in results}
    assert "case-b" in run_ids


def test_recall_search_flags_stale_corpus(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall_search import recall_search

    index_dir, lessons_path = _build_fixture_corpus(tmp_path)
    corpus_path = index_dir / "corpus.jsonl"
    # Bump lessons mtime far ahead so the corpus is stale.
    future = time.time() + 7200
    os.utime(lessons_path, (future, future))

    results = recall_search(
        "peak flow",
        top_k=3,
        index_dir=index_dir,
        corpus_path=corpus_path,
        lessons_path=lessons_path,
    )
    assert results
    assert "stale" in str(results[0].get("warning", "")).lower()


def test_recall_search_missing_corpus_returns_empty_list(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall_search import recall_search

    results = recall_search(
        "any",
        top_k=3,
        index_dir=tmp_path / "missing",
        corpus_path=tmp_path / "missing" / "corpus.jsonl",
        lessons_path=tmp_path / "missing" / "lessons.md",
    )
    assert results == []


def test_recall_search_refuses_mixed_schema_versions(tmp_path: Path) -> None:
    from agentic_swmm.memory.recall_search import recall_search

    index_dir, lessons_path = _build_fixture_corpus(tmp_path)
    corpus_path = index_dir / "corpus.jsonl"
    # Rewrite lessons with a different schema_version marker.
    lessons_path.write_text(
        "<!-- schema_version: 1.0 -->\n# Lessons\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"schema"):
        recall_search(
            "anything",
            top_k=3,
            index_dir=index_dir,
            corpus_path=corpus_path,
            lessons_path=lessons_path,
        )
