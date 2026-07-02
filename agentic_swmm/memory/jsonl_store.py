"""Shared JSONL file mechanics for the memory layer.

Every JSONL-backed store used to hand-roll the same idioms — mkdir →
``json.dumps`` → append a line; missing file → nothing; skip blank and
torn lines on read. This module owns the file mechanics once; each
domain store keeps its own schema validation, record migration and
filtering on top.

This module deliberately does NOT merge any stores and does NOT touch
any mutation rule (Key invariant 4: modeling memory mutates only via
explicit verbs). Serialization flags are parameters because two stores
intentionally differ from the ``sort_keys=True, ensure_ascii=False``
default (the audit-hook skip log keeps insertion order; the
context-budget trace keeps ASCII escaping).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


def dump_line(
    payload: Any, *, sort_keys: bool = True, ensure_ascii: bool = False
) -> str:
    """Serialize one row exactly the way the memory stores do (no newline)."""
    return json.dumps(payload, ensure_ascii=ensure_ascii, sort_keys=sort_keys)


def append_row(
    path: Path,
    payload: Any,
    *,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
) -> None:
    """Append one JSONL row, creating parent directories on first write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            dump_line(payload, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
            + "\n"
        )


def append_rows(
    path: Path,
    payloads: Iterable[Any],
    *,
    sort_keys: bool = True,
    ensure_ascii: bool = False,
) -> None:
    """Append many rows under a single ``open()`` (one flush window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(
                dump_line(
                    payload, sort_keys=sort_keys, ensure_ascii=ensure_ascii
                )
                + "\n"
            )


def iter_rows(path: Path) -> Iterator[Any]:
    """Yield parsed rows from ``path``.

    Missing file yields nothing (callers get their ``[]`` for free);
    blank lines are skipped; unparseable lines are skipped because a
    torn final line happens when a reader overlaps a concurrent append —
    tolerate it, never raise. Rows are yielded as whatever JSON parsed
    to — callers that require dicts filter with ``isinstance`` (only
    the archive reader historically did).
    """
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                # Torn final line during a concurrent write — skip.
                continue


__all__ = ["append_row", "append_rows", "dump_line", "iter_rows"]
