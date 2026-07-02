"""Locate a Run's artifacts (manifest / INP / OUT) inside its run dir.

This is the run-dir layout contract in code form: manifest-recorded
paths win, then the conventional subdirectory patterns, then a
recursive fallback. It lived as private helpers inside the ``plot``
CLI verb and was reached into by the ``map`` verb and the plot tool
handler — three consumers importing underscore names across module
boundaries. The 2026-07 architecture pass gave the family its own
home next to the other run-contract modules (postflight / rpt_summary).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentic_swmm.utils.paths import repo_root


def read_manifest(run_dir: Path) -> dict[str, Any]:
    """Return the run's manifest dict, or ``{}`` when none parses."""
    candidates = [run_dir / "manifest.json", *sorted(run_dir.glob("**/manifest.json"))]
    for path in candidates:
        if path.exists():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            return parsed if isinstance(parsed, dict) else {}
    return {}


def resolve_recorded_path(value: str | None, run_dir: Path) -> Path | None:
    """Resolve a manifest-recorded path: absolute wins, then run-dir
    relative, then repo-root relative."""
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    candidate = run_dir / path
    if candidate.exists():
        return candidate
    return repo_root() / path


def find_inp(run_dir: Path, manifest: dict[str, Any]) -> Path | None:
    """Locate the run's INP: manifest-recorded first, then conventions."""
    recorded = resolve_recorded_path(manifest.get("inp"), run_dir)
    if recorded and recorded.exists():
        return recorded
    for pattern in ("00_inputs/*.inp", "04_builder/*.inp", "*.inp", "**/*.inp"):
        matches = sorted(run_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def find_out(run_dir: Path, manifest: dict[str, Any]) -> Path | None:
    """Locate the run's binary OUT: manifest-recorded first, then conventions."""
    files = manifest.get("files")
    if isinstance(files, dict):
        recorded = resolve_recorded_path(files.get("out"), run_dir)
        if recorded and recorded.exists():
            return recorded
    for pattern in ("05_runner/*.out", "01_runner/*.out", "*.out", "**/*.out"):
        matches = sorted(run_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


__all__ = ["find_inp", "find_out", "read_manifest", "resolve_recorded_path"]
