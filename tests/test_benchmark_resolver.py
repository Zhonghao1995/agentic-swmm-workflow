"""Tests for ``agentic_swmm.memory.benchmark_resolver`` (PRD-07 Phase 4).

The resolver layers a project-local overlay over the curated library
so a maintainer can tighten a single threshold without forking the
default benchmarks YAML. Resolution order is overlay -> library ->
caller default; missing files at any layer are non-fatal.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.benchmark_resolver import (
    PROJECT_OVERRIDES_FILENAME,
    default_project_overrides_path,
    load_project_overrides,
    resolve_threshold,
)


_LIBRARY_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
  flow:
    warn: 1.0
    fail: 5.0
nse_acceptable_thresholds:
  stormwater_event:
    acceptable: null
    good: null
"""


_OVERRIDES_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 0.5
    fail: 2.0
"""


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class ResolutionOrderTests(unittest.TestCase):
    def test_no_overrides_returns_library_value(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = _write(Path(tmp) / "lib.yaml", _LIBRARY_YAML)
            value = resolve_threshold(
                "continuity_thresholds_pct.runoff.warn",
                reference_benchmarks_path=lib,
                project_overrides_path=None,
                default=99.0,
            )
        self.assertEqual(value, 5.0)

    def test_override_present_wins_over_library(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = _write(Path(tmp) / "lib.yaml", _LIBRARY_YAML)
            overrides = _write(Path(tmp) / "overrides.yaml", _OVERRIDES_YAML)
            value = resolve_threshold(
                "continuity_thresholds_pct.runoff.warn",
                reference_benchmarks_path=lib,
                project_overrides_path=overrides,
                default=99.0,
            )
        self.assertEqual(value, 0.5)

    def test_override_resolves_dict_leaf_in_full(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = _write(Path(tmp) / "lib.yaml", _LIBRARY_YAML)
            overrides = _write(Path(tmp) / "overrides.yaml", _OVERRIDES_YAML)
            value = resolve_threshold(
                "continuity_thresholds_pct.runoff",
                reference_benchmarks_path=lib,
                project_overrides_path=overrides,
                default={},
            )
        self.assertEqual(value, {"warn": 0.5, "fail": 2.0})

    def test_library_null_falls_to_caller_default(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = _write(Path(tmp) / "lib.yaml", _LIBRARY_YAML)
            value = resolve_threshold(
                "nse_acceptable_thresholds.stormwater_event.good",
                reference_benchmarks_path=lib,
                default=0.65,
            )
        self.assertEqual(value, 0.65)

    def test_missing_key_falls_to_caller_default(self) -> None:
        with TemporaryDirectory() as tmp:
            lib = _write(Path(tmp) / "lib.yaml", _LIBRARY_YAML)
            value = resolve_threshold(
                "continuity_thresholds_pct.does_not_exist.warn",
                reference_benchmarks_path=lib,
                default=99.9,
            )
        self.assertEqual(value, 99.9)

    def test_missing_files_graceful(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            value = resolve_threshold(
                "anything.at.all",
                reference_benchmarks_path=tmp_path / "absent_lib.yaml",
                project_overrides_path=tmp_path / "absent_overrides.yaml",
                default=42,
            )
        self.assertEqual(value, 42)

    def test_only_override_present_no_library(self) -> None:
        with TemporaryDirectory() as tmp:
            overrides = _write(Path(tmp) / "overrides.yaml", _OVERRIDES_YAML)
            value = resolve_threshold(
                "continuity_thresholds_pct.runoff.warn",
                reference_benchmarks_path=None,
                project_overrides_path=overrides,
                default=99.0,
            )
        self.assertEqual(value, 0.5)


class OverridesPathTests(unittest.TestCase):
    def test_default_path_under_memory_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "modeling-memory"
            memory_dir.mkdir()
            path = default_project_overrides_path(memory_dir)
            self.assertEqual(path.name, PROJECT_OVERRIDES_FILENAME)
            self.assertEqual(path.parent, memory_dir)

    def test_default_path_no_arg_uses_repo_layout(self) -> None:
        # Should not raise; concrete path depends on repo location.
        path = default_project_overrides_path()
        self.assertEqual(path.name, PROJECT_OVERRIDES_FILENAME)

    def test_load_project_overrides_missing_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            absent = Path(tmp) / PROJECT_OVERRIDES_FILENAME
            self.assertEqual(load_project_overrides(absent), {})

    def test_load_project_overrides_parses_valid_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            path = _write(Path(tmp) / PROJECT_OVERRIDES_FILENAME, _OVERRIDES_YAML)
            data = load_project_overrides(path)
        self.assertIn("continuity_thresholds_pct", data)


class OverlayNullLeafFallthroughTests(unittest.TestCase):
    """Overlay leaf set to ``null`` must not mask the library value."""

    def test_overlay_null_leaf_falls_through_to_library(self) -> None:
        overrides_with_null = (
            "continuity_thresholds_pct:\n  runoff:\n    warn: null\n"
        )
        with TemporaryDirectory() as tmp:
            lib = _write(Path(tmp) / "lib.yaml", _LIBRARY_YAML)
            overrides = _write(
                Path(tmp) / "overrides.yaml", overrides_with_null
            )
            value = resolve_threshold(
                "continuity_thresholds_pct.runoff.warn",
                reference_benchmarks_path=lib,
                project_overrides_path=overrides,
                default=99.0,
            )
        self.assertEqual(value, 5.0)


if __name__ == "__main__":
    unittest.main()
