"""Tests that ``gather_memory_context`` honours the project overlay (PRD-07 Phase 4).

The agent's read-side memory snapshot must surface the overlay-aware
threshold dict in ``reference_thresholds`` so a downstream decision
(disambiguator, QA gate proposal) sees the same number the runtime
gate would apply.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.memory_context import gather_memory_context


_LIBRARY_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
  flow:
    warn: 1.0
    fail: 5.0
"""


_OVERRIDES_YAML = """
continuity_thresholds_pct:
  runoff:
    warn: 0.5
    fail: 2.0
"""


class MemoryContextOverlayTests(unittest.TestCase):
    def test_overlay_shadows_library_in_context(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "modeling-memory"
            memory_dir.mkdir()
            (memory_dir / "reference_benchmarks.yaml").write_text(
                _LIBRARY_YAML, encoding="utf-8"
            )
            (memory_dir / "project_overrides.yaml").write_text(
                _OVERRIDES_YAML, encoding="utf-8"
            )
            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich",
                metrics_of_interest=("runoff_continuity_pct",),
            )
        runoff = ctx.reference_thresholds["runoff_continuity_pct"]
        self.assertEqual(runoff["warn"], 0.5)
        self.assertEqual(runoff["fail"], 2.0)
        # Provenance records the overlay path.
        self.assertIn("project_overrides_path", ctx.provenance)

    def test_no_overlay_falls_back_to_library(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "modeling-memory"
            memory_dir.mkdir()
            (memory_dir / "reference_benchmarks.yaml").write_text(
                _LIBRARY_YAML, encoding="utf-8"
            )
            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich",
                metrics_of_interest=("runoff_continuity_pct",),
            )
        runoff = ctx.reference_thresholds["runoff_continuity_pct"]
        self.assertEqual(runoff["warn"], 5.0)


if __name__ == "__main__":
    unittest.main()
