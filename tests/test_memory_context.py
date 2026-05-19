"""Tests for ``agentic_swmm.agent.memory_context`` (PRD-07 Phase 1).

The memory read interface answers, in plain Python, "what does this
project's history say about the case I am about to run?" — without
mutating any of the stores it consults. It returns a populated
:class:`MemoryContext` even when there are zero hits, so downstream
callers can branch on field contents rather than on
``None``/exception. The runtime never has to special-case "memory not
initialised yet".

Slices covered here:

1. Empty :class:`MemoryContext` construction.
2. :func:`gather_memory_context` against an empty memory dir.
3. One parametric hit feeds through.
4. Summary string aggregates N hits with simple metric stats.
5. ``reference_thresholds`` prefill from the YAML benchmarks file.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.memory_context import (
    MemoryContext,
    ParametricRecord,
    gather_memory_context,
)
from agentic_swmm.memory.parametric_memory import (
    ParametricRecord as StoredParametricRecord,
    record_parametric_run,
)


_BENCHMARK_YAML = """
schema_version: "1.0"

continuity_thresholds_pct:
  runoff:
    warn: 5.0
    fail: 10.0
  flow:
    warn: 1.0
    fail: 5.0
"""


def _seed_parametric(memory_dir: Path, **overrides: object) -> None:
    """Write one parametric_memory.jsonl row into ``memory_dir``."""
    defaults: dict[str, object] = {
        "run_id": "run-x",
        "case_name": "saanich-b8",
        "swmm_version": "5.2.4",
        "model_structure": {"use_case": "stormwater_event"},
        "qa_metrics": {"runoff_continuity_pct": 0.5},
        "performance_metrics": {},
        "watershed_classification": {},
    }
    defaults.update(overrides)
    record = StoredParametricRecord(**defaults)  # type: ignore[arg-type]
    record_parametric_run(memory_dir / "parametric_memory.jsonl", record)


class MemoryContextDataclassTests(unittest.TestCase):
    """Slice 1 — the dataclass must construct empty."""

    def test_empty_construction_has_safe_defaults(self) -> None:
        ctx = MemoryContext()
        self.assertEqual(ctx.parametric_hits, [])
        self.assertEqual(ctx.reference_thresholds, {})
        self.assertEqual(ctx.summary, "")
        self.assertIsInstance(ctx.provenance, dict)

    def test_is_empty_helper_reports_population(self) -> None:
        ctx = MemoryContext()
        self.assertTrue(ctx.is_empty())
        ctx.parametric_hits.append(ParametricRecord(run_id="r", case_name="c"))
        self.assertFalse(ctx.is_empty())


class GatherMemoryContextEmptyDirTests(unittest.TestCase):
    """Slice 2 — missing memory files must yield an empty MemoryContext, not raise."""

    def test_no_memory_dir_returns_empty_context(self) -> None:
        with TemporaryDirectory() as tmp:
            ctx = gather_memory_context(
                memory_dir=Path(tmp) / "does-not-exist",
                case_name="saanich-b8",
            )
        self.assertEqual(ctx.parametric_hits, [])
        self.assertEqual(ctx.reference_thresholds, {})
        self.assertTrue(ctx.is_empty())
        self.assertIn("memory_dir", ctx.provenance)

    def test_missing_parametric_file_does_not_raise(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            ctx = gather_memory_context(
                memory_dir=memory_dir, case_name="saanich-b8"
            )
        self.assertEqual(ctx.parametric_hits, [])
        self.assertEqual(ctx.parametric_hit_count, 0)


class GatherMemoryContextPopulatedTests(unittest.TestCase):
    """Slice 3 — one parametric hit must surface as a ParametricRecord."""

    def test_single_hit_for_case_name(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            _seed_parametric(memory_dir, run_id="r1", case_name="saanich-b8")
            _seed_parametric(memory_dir, run_id="r2", case_name="tecnopolo")

            ctx = gather_memory_context(
                memory_dir=memory_dir, case_name="saanich-b8"
            )

        self.assertEqual(ctx.parametric_hit_count, 1)
        self.assertEqual(ctx.parametric_hits[0].run_id, "r1")
        self.assertEqual(ctx.parametric_hits[0].case_name, "saanich-b8")

    def test_use_case_filter_narrows_further(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            _seed_parametric(
                memory_dir,
                run_id="evt",
                model_structure={"use_case": "stormwater_event"},
            )
            _seed_parametric(
                memory_dir,
                run_id="cont",
                model_structure={"use_case": "stormwater_continuous"},
            )

            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich-b8",
                use_case="stormwater_event",
            )

        self.assertEqual(ctx.parametric_hit_count, 1)
        self.assertEqual(ctx.parametric_hits[0].run_id, "evt")


class GatherMemoryContextSummaryTests(unittest.TestCase):
    """Slice 4 — the summary string is the one-line LLM-readable digest."""

    def test_summary_describes_zero_hits(self) -> None:
        with TemporaryDirectory() as tmp:
            ctx = gather_memory_context(
                memory_dir=Path(tmp), case_name="saanich-b8"
            )
        self.assertIn("0", ctx.summary)
        self.assertIn("saanich-b8", ctx.summary)

    def test_summary_reports_count_and_continuity_mean(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            for run_id, runoff in [("a", 0.5), ("b", 1.0), ("c", 1.5)]:
                _seed_parametric(
                    memory_dir,
                    run_id=run_id,
                    qa_metrics={"runoff_continuity_pct": runoff},
                )

            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich-b8",
                metrics_of_interest=("runoff_continuity_pct",),
            )

        self.assertIn("3", ctx.summary)
        self.assertIn("saanich-b8", ctx.summary)
        # Mean continuity 1.0 should appear in the summary.
        self.assertIn("1.0", ctx.summary)


class GatherMemoryContextReferenceThresholdsTests(unittest.TestCase):
    """Slice 5 — reference thresholds prefill from the benchmarks YAML."""

    def test_thresholds_loaded_for_metrics_of_interest(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            (memory_dir / "reference_benchmarks.yaml").write_text(
                _BENCHMARK_YAML, encoding="utf-8"
            )

            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich-b8",
                metrics_of_interest=(
                    "runoff_continuity_pct",
                    "flow_continuity_pct",
                ),
            )

        self.assertIn("runoff_continuity_pct", ctx.reference_thresholds)
        self.assertEqual(
            ctx.reference_thresholds["runoff_continuity_pct"]["warn"], 5.0
        )
        self.assertEqual(
            ctx.reference_thresholds["runoff_continuity_pct"]["fail"], 10.0
        )

    def test_missing_threshold_omits_metric_silently(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            (memory_dir / "reference_benchmarks.yaml").write_text(
                _BENCHMARK_YAML, encoding="utf-8"
            )

            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich-b8",
                metrics_of_interest=("nse",),
            )

        self.assertNotIn("nse", ctx.reference_thresholds)

    def test_provenance_records_paths_and_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp)
            _seed_parametric(memory_dir, run_id="r1", case_name="saanich-b8")
            (memory_dir / "reference_benchmarks.yaml").write_text(
                _BENCHMARK_YAML, encoding="utf-8"
            )

            ctx = gather_memory_context(
                memory_dir=memory_dir,
                case_name="saanich-b8",
                metrics_of_interest=("runoff_continuity_pct",),
            )

        self.assertEqual(ctx.provenance["parametric_hit_count"], 1)
        self.assertIn("parametric_memory_path", ctx.provenance)
        self.assertIn("reference_benchmarks_path", ctx.provenance)
        self.assertIn("gathered_at_utc", ctx.provenance)

    def test_read_only_does_not_create_files(self) -> None:
        """gather_memory_context must never mutate the store."""
        with TemporaryDirectory() as tmp:
            memory_dir = Path(tmp) / "fresh"
            gather_memory_context(memory_dir=memory_dir, case_name="x")
            # Empty memory_dir should not be created by the read path.
            self.assertFalse(memory_dir.exists())


if __name__ == "__main__":
    unittest.main()
