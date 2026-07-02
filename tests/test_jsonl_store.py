"""Contract tests for the shared JSONL file mechanics (memory/jsonl_store.py).

The domain stores compose their validation/migration/filtering on top of
these primitives, so this file pins the mechanics every store now relies
on: serialization flags, parent-dir creation, torn-line tolerance, and
missing-file behaviour.
"""
from __future__ import annotations

from pathlib import Path

from agentic_swmm.memory.jsonl_store import (
    append_row,
    append_rows,
    dump_line,
    iter_rows,
)


def test_round_trip_defaults(tmp_path: Path) -> None:
    store = tmp_path / "nested" / "store.jsonl"
    append_row(store, {"b": 1, "a": 2})
    append_row(store, {"z": "值"})
    rows = list(iter_rows(store))
    assert rows == [{"b": 1, "a": 2}, {"z": "值"}]
    text = store.read_text(encoding="utf-8")
    # Default flags: sorted keys, no ASCII escaping — byte-compatible with
    # what the stores wrote before the consolidation.
    assert text == '{"a": 2, "b": 1}\n{"z": "值"}\n'


def test_flag_variants_match_legacy_bytes(tmp_path: Path) -> None:
    # audit-hook skip log: insertion order preserved.
    assert dump_line({"b": 1, "a": 2}, sort_keys=False) == '{"b": 1, "a": 2}'
    # context-budget trace: ASCII escaping preserved.
    assert dump_line({"z": "值"}, ensure_ascii=True) == '{"z": "\\u503c"}'


def test_append_rows_batch(tmp_path: Path) -> None:
    store = tmp_path / "batch.jsonl"
    append_rows(store, ({"i": i} for i in range(3)))
    assert [row["i"] for row in iter_rows(store)] == [0, 1, 2]


def test_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(iter_rows(tmp_path / "absent.jsonl")) == []


def test_torn_final_line_and_blanks_are_skipped(tmp_path: Path) -> None:
    store = tmp_path / "torn.jsonl"
    store.write_text('{"ok": 1}\n\n{"ok": 2}\n{"torn": ', encoding="utf-8")
    assert list(iter_rows(store)) == [{"ok": 1}, {"ok": 2}]


def test_non_dict_rows_are_yielded_not_filtered(tmp_path: Path) -> None:
    """Dict filtering is the caller's job (only the archive reader wants it)."""
    store = tmp_path / "mixed.jsonl"
    store.write_text('[1, 2]\n{"d": 1}\n', encoding="utf-8")
    assert list(iter_rows(store)) == [[1, 2], {"d": 1}]
