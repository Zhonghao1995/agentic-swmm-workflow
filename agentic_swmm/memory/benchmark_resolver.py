"""Threshold resolver with project-local overlay (PRD-07 Phase 4).

The project ships a curated default library at
``memory/modeling-memory/reference_benchmarks.yaml``. A maintainer or a
downstream user may, however, want to tighten or relax a single
threshold without forking the library — e.g. an event-only study where
0.5% runoff continuity is the working tolerance, not 5%.

This module exposes one entry point — :func:`resolve_threshold` — that
walks three layers in order:

1. ``project_overrides_path`` (optional file, same shape as
   ``reference_benchmarks.yaml``). When the dotted key is present **and
   not** ``None`` here, it wins.
2. The default library at ``reference_benchmarks_path``. When the leaf
   is present and not ``None`` here, it wins.
3. The caller's ``default`` argument. Always wins last.

The contract is intentionally narrow:

- Missing files at any layer are non-fatal (mirrors
  ``recall_reference_benchmark`` semantics).
- A leaf that exists but is ``None`` (the Phase A "un-cited placeholder"
  pattern) is treated as *absent* so the next layer takes over. The
  reader never returns ``None`` as a real threshold — Phase A's
  intentional nulls would otherwise leak into the runtime.
- The caller's ``default`` is what preserves today's hard-coded
  behaviour while the YAML is still incomplete. When the maintainer
  later fills the YAML, the library wins automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentic_swmm.memory.reference_benchmarks import (
    load_reference_benchmarks,
    recall_reference_benchmark,
)


PROJECT_OVERRIDES_FILENAME = "project_overrides.yaml"


def resolve_threshold(
    dotted_key: str,
    *,
    reference_benchmarks_path: Path | None = None,
    project_overrides_path: Path | None = None,
    default: Any = None,
) -> Any:
    """Resolve ``dotted_key`` against project overlay → library → default.

    Arguments:
        dotted_key: Path through the YAML, e.g.
            ``"continuity_thresholds_pct.runoff.warn"``.
        reference_benchmarks_path: Optional path to the default library
            YAML. ``None`` means "no library consulted"; the resolver
            falls straight through to ``default`` if the override also
            misses.
        project_overrides_path: Optional path to the per-project overlay
            YAML. When present and the key resolves to a non-``None``
            value, this wins.
        default: Caller's hard-coded fallback. Returned when neither
            file resolves a non-``None`` leaf for ``dotted_key``. The
            type is :data:`typing.Any` so callers can pass dicts (for
            ``{"warn": ..., "fail": ...}`` lookups) or scalars.

    Returns:
        The first non-``None`` value found, or ``default`` if none.
    """
    if project_overrides_path is not None:
        value = recall_reference_benchmark(
            Path(project_overrides_path), dotted_key, default=None
        )
        if value is not None:
            return value

    if reference_benchmarks_path is not None:
        value = recall_reference_benchmark(
            Path(reference_benchmarks_path), dotted_key, default=None
        )
        if value is not None:
            return value

    return default


def default_project_overrides_path(memory_dir: Path | None = None) -> Path:
    """Return the conventional path for the project overrides file.

    Callers typically construct this once per invocation:
    ``default_project_overrides_path(Path("memory/modeling-memory"))``.
    The file does not need to exist — :func:`resolve_threshold` treats
    a missing override file as "no overrides registered".
    """
    if memory_dir is None:
        # Two parents up from this module: agentic_swmm/memory/ ->
        # agentic_swmm/ -> repo root.
        repo_root = Path(__file__).resolve().parents[2]
        memory_dir = repo_root / "memory" / "modeling-memory"
    return Path(memory_dir) / PROJECT_OVERRIDES_FILENAME


def load_project_overrides(path: Path) -> dict[str, Any]:
    """Return the parsed overlay YAML, or ``{}`` on any failure.

    Thin wrapper around :func:`load_reference_benchmarks` — present so
    callers that want to inspect the overlay (e.g. for diagnostics)
    have a stable symbol on the resolver module rather than reaching
    back into the reference-benchmarks loader.
    """
    return load_reference_benchmarks(path)


__all__ = [
    "PROJECT_OVERRIDES_FILENAME",
    "default_project_overrides_path",
    "load_project_overrides",
    "resolve_threshold",
]
