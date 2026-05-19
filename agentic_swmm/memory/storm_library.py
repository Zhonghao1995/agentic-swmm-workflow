"""Storm library loader (PRD-06 §4.4 — Round 2).

The project ships a curated event store at
``memory/modeling-memory/storm_library.yaml``. The schema separates four
sub-blocks:

* ``chicago_hyetographs`` — region/return-period entries with
  ``idf_params``, ``peak_position`` and metadata. These drive the
  ``aiswmm storm --from-library <key>`` CLI surface.
* ``huff_user_overrides`` — optional user-supplied regional variants
  of the dimensionless Huff distributions (the in-code defaults stay
  the source of truth; this block exists for project-local tuning).
* ``scs_user_overrides`` — same pattern for SCS Type II.
* ``user_curated`` — free-form historical / recorded events.

This module is a small typed reader, mirroring the small-facade pattern
used by :mod:`agentic_swmm.memory.citations`. Three verbs:

- :func:`load_storm_library` — parse the YAML, tolerant of missing /
  malformed files
- :func:`recall_chicago_spec` — single-entry lookup for the Chicago
  block (the most commonly recalled shape)
- :func:`recall_user_curated` — single-entry lookup for the curated
  events block
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_storm_library(path: Path) -> dict[str, Any]:
    """Load the storm library YAML; return ``{}`` on any failure.

    Missing files and malformed YAML both yield ``{}`` so a fresh
    project (no library yet) does not need a special-case branch. The
    top-level ``schema_version`` is left in the returned dict so the
    caller can negotiate future migrations.
    """
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def recall_chicago_spec(path: Path, key: str) -> dict[str, Any] | None:
    """Return a single Chicago-block entry or ``None``.

    Returns ``None`` when ``key`` is empty, the library cannot be
    loaded, or the entry exists but its leaf fields are all ``None``
    (a schema-only placeholder). The caller is expected to inspect
    ``idf_params`` / ``peak_position`` / ``duration_min`` before
    handing the entry to :func:`chicago_hyetograph`.
    """
    if not key or not str(key).strip():
        return None
    lib = load_storm_library(path)
    block = lib.get("chicago_hyetographs") or {}
    if not isinstance(block, dict):
        return None
    entry = block.get(str(key).strip())
    if not isinstance(entry, dict):
        return None
    # Treat an entry whose values are all ``None`` as missing — this
    # mirrors the placeholder convention used by reference_benchmarks.yaml
    # so the runtime never tries to build a storm from null params.
    if _is_all_null(entry):
        return None
    return dict(entry)


def recall_user_curated(path: Path, key: str) -> dict[str, Any] | None:
    """Return a single user-curated event or ``None``."""
    if not key or not str(key).strip():
        return None
    lib = load_storm_library(path)
    block = lib.get("user_curated") or {}
    if not isinstance(block, dict):
        return None
    entry = block.get(str(key).strip())
    if not isinstance(entry, dict):
        return None
    if _is_all_null(entry):
        return None
    return dict(entry)


def _is_all_null(entry: dict[str, Any]) -> bool:
    """Return True when every value in ``entry`` is None or all-null sub-dict.

    A nested ``idf_params: {a: null, b: null, c: null}`` block counts as
    null so we can distinguish "schema entry present" from "values
    populated". Empty dicts also count as null.
    """
    if not entry:
        return True
    for value in entry.values():
        if value is None:
            continue
        if isinstance(value, dict) and _is_all_null(value):
            continue
        return False
    return True


__all__ = [
    "load_storm_library",
    "recall_chicago_spec",
    "recall_user_curated",
]
