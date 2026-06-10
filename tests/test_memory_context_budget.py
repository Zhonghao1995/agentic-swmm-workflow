"""Tests for ``agentic_swmm.memory.context_budget`` (P0-1).

Coverage:
- Under-budget: passthrough — output is byte-identical to joining entries.
- Over-budget: correct packing order, exclusion event, availability note.
- Giant single entry: head-truncated, availability note present.
- Zero-budget: treated as unlimited, no note.
- Empty entries: empty result.
- Trace event written to the correct path with the right fields.
- Config keys parse from toml (integration smoke test).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.memory.context_budget import (
    DEFAULT_CONTEXT_BUDGET_CHARS,
    BudgetResult,
    MemoryEntry,
    apply_context_budget,
    emit_budget_trace_event,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(id: str, text: str, relevance: float = 0.0) -> MemoryEntry:
    return MemoryEntry(id=id, text=text, relevance=relevance)


# ---------------------------------------------------------------------------
# Under-budget: passthrough (lock-in test)
# ---------------------------------------------------------------------------


def test_under_budget_passthrough_text_identical() -> None:
    """When entries comfortably fit the budget, output equals the joined texts."""
    e1 = _entry("a", "hello")
    e2 = _entry("b", "world")
    result = apply_context_budget([e1, e2], budget=DEFAULT_CONTEXT_BUDGET_CHARS)

    # No entries excluded.
    assert result.excluded_count == 0
    assert result.excluded_ids == []
    # No availability note appended.
    assert "recall on demand" not in result.injected_text
    # Text contains both entries.
    assert "hello" in result.injected_text
    assert "world" in result.injected_text
    # Ordering: both entries have relevance=0.0, so input order is preserved.
    assert result.injected_text.index("hello") < result.injected_text.index("world")
    assert not result.truncated_head


def test_under_budget_injected_ids_match_input_order() -> None:
    e1 = _entry("x", "foo")
    e2 = _entry("y", "bar")
    result = apply_context_budget([e1, e2], budget=5000)
    assert result.injected_ids == ["x", "y"]


# ---------------------------------------------------------------------------
# Over-budget: correct packing order, exclusion, availability note
# ---------------------------------------------------------------------------


def test_over_budget_higher_relevance_wins() -> None:
    """Higher relevance entries are injected; lower-relevance ones are excluded."""
    low = _entry("low", "A" * 100, relevance=0.5)
    high = _entry("high", "B" * 100, relevance=1.0)
    # Budget fits only one 100-char entry (plus a bit of headroom for the note).
    result = apply_context_budget([low, high], budget=150)

    assert "high" in result.injected_ids
    assert "low" in result.excluded_ids
    assert result.excluded_count == 1


def test_over_budget_availability_note_present() -> None:
    e1 = _entry("e1", "X" * 200, relevance=1.0)
    e2 = _entry("e2", "Y" * 200, relevance=0.5)
    result = apply_context_budget([e1, e2], budget=250)

    assert "recall on demand" in result.injected_text
    assert result.excluded_count >= 1


def test_over_budget_note_counts_excluded_entries() -> None:
    entries = [_entry(f"e{i}", "Z" * 300, relevance=float(i)) for i in range(5)]
    result = apply_context_budget(entries, budget=400)
    n_excluded = result.excluded_count
    expected_note = f"({n_excluded} more memory entries available — recall on demand)"
    assert expected_note in result.injected_text


def test_over_budget_excluded_ids_listed_correctly() -> None:
    entries = [_entry(f"e{i}", "W" * 200, relevance=float(5 - i)) for i in range(4)]
    result = apply_context_budget(entries, budget=250)
    assert set(result.excluded_ids).issubset({e.id for e in entries})
    assert set(result.injected_ids).issubset({e.id for e in entries})
    assert set(result.injected_ids) | set(result.excluded_ids) == {e.id for e in entries}


# ---------------------------------------------------------------------------
# Giant single entry: head-truncated
# ---------------------------------------------------------------------------


def test_giant_single_entry_head_truncated() -> None:
    """A single entry larger than the whole budget is head-truncated."""
    big = _entry("big", "G" * 5000)
    result = apply_context_budget([big], budget=200)

    assert result.truncated_head
    # Injected text is shorter than the original.
    assert len(result.injected_text) <= 200 + 50  # +50 for the note
    # Availability note is still present.
    assert "recall on demand" in result.injected_text
    # Entry id is in injected_ids (it was head-injected, not excluded).
    assert "big" in result.injected_ids


def test_giant_single_entry_excluded_count_is_zero_for_others() -> None:
    """When there is only one giant entry, excluded_count can be 0 (it's injected truncated)."""
    big = _entry("only", "H" * 5000)
    result = apply_context_budget([big], budget=300)
    # The giant entry is in injected_ids (as a head truncation), not excluded.
    assert "only" in result.injected_ids


# ---------------------------------------------------------------------------
# Zero/negative budget: unlimited passthrough
# ---------------------------------------------------------------------------


def test_zero_budget_is_unlimited() -> None:
    entries = [_entry(f"e{i}", "T" * 1000) for i in range(3)]
    result = apply_context_budget(entries, budget=0)
    assert result.excluded_count == 0
    assert "recall on demand" not in result.injected_text
    for e in entries:
        assert e.id in result.injected_ids


def test_negative_budget_is_unlimited() -> None:
    e = _entry("x", "content")
    result = apply_context_budget([e], budget=-100)
    assert result.excluded_count == 0


# ---------------------------------------------------------------------------
# Empty entries
# ---------------------------------------------------------------------------


def test_empty_entries_returns_empty_result() -> None:
    result = apply_context_budget([], budget=4000)
    assert result.injected_text == ""
    assert result.excluded_count == 0
    assert result.injected_ids == []


# ---------------------------------------------------------------------------
# Trace event
# ---------------------------------------------------------------------------


def test_emit_budget_trace_event_writes_correct_fields(tmp_path: Path) -> None:
    trace = tmp_path / "agent_trace.jsonl"
    result = BudgetResult(
        injected_text="some text",
        injected_ids=["a", "b"],
        excluded_ids=["c"],
        excluded_count=1,
        truncated_head=False,
    )
    emit_budget_trace_event(trace, result, budget_chars=4000)

    assert trace.exists()
    event = json.loads(trace.read_text(encoding="utf-8").strip())
    assert event["event"] == "memory_context_budget"
    assert event["budget_chars"] == 4000
    assert event["injected_count"] == 2
    assert event["excluded_count"] == 1
    assert event["excluded_ids"] == ["c"]
    assert event["truncated_head"] is False
    assert "timestamp_utc" in event


def test_emit_budget_trace_event_appends(tmp_path: Path) -> None:
    trace = tmp_path / "agent_trace.jsonl"
    result = BudgetResult(injected_text="", injected_ids=[], excluded_ids=[], excluded_count=0)
    emit_budget_trace_event(trace, result, budget_chars=4000)
    emit_budget_trace_event(trace, result, budget_chars=4000)
    lines = [l for l in trace.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2


def test_emit_budget_trace_event_creates_parent_dirs(tmp_path: Path) -> None:
    trace = tmp_path / "deep" / "nested" / "agent_trace.jsonl"
    result = BudgetResult(injected_text="", injected_ids=[], excluded_ids=[], excluded_count=0)
    emit_budget_trace_event(trace, result, budget_chars=4000)
    assert trace.exists()


# ---------------------------------------------------------------------------
# Config keys parse from toml (smoke test)
# ---------------------------------------------------------------------------


def test_config_memory_context_budget_key_parses(tmp_path: Path, monkeypatch) -> None:
    """memory.context_budget_chars can be set and read back from toml."""
    import os

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "[memory]\ncontext_budget_chars = 2000\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AISWMM_CONFIG_DIR", str(config_dir))

    from importlib import reload
    import agentic_swmm.config as _cfg_mod

    cfg = _cfg_mod.load_config(config_file)
    assert cfg.get("memory.context_budget_chars") == 2000


def test_config_recall_half_life_days_key_parses(tmp_path: Path, monkeypatch) -> None:
    """memory.recall_half_life_days can be set and read back from toml."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "[memory]\nrecall_half_life_days = 30\n",
        encoding="utf-8",
    )
    from agentic_swmm.config import load_config

    cfg = load_config(config_file)
    assert cfg.get("memory.recall_half_life_days") == 30


def test_config_defaults_present() -> None:
    """Both new memory config keys have valid defaults in default_values()."""
    from agentic_swmm.config import default_values

    dv = default_values()
    memory = dv.get("memory", {})
    assert "context_budget_chars" in memory
    assert "recall_half_life_days" in memory
    assert memory["context_budget_chars"] == DEFAULT_CONTEXT_BUDGET_CHARS
    assert memory["recall_half_life_days"] == 0
