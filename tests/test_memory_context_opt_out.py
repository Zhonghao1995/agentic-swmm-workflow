"""``gather_memory_context`` short-circuit under the opt-out env var.

When ``AISWMM_DISABLE_MEMORY_INFORMED=1`` is set the function must
return an empty :class:`MemoryContext` *without* reading any store,
regardless of what the on-disk files contain. The provenance
carries a ``disabled`` marker so the audit trail can still record
why the consultation was skipped.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentic_swmm.agent.feature_flags import MEMORY_INFORMED_ENV
from agentic_swmm.agent.memory_context import gather_memory_context


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


class GatherMemoryContextOptOutTests(unittest.TestCase):
    """The opt-out flag wins regardless of store contents."""

    def test_disabled_returns_empty_context_when_store_has_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem_dir = Path(tmp) / "modeling-memory"
            mem_dir.mkdir(parents=True)
            _write_jsonl(
                mem_dir / "parametric_memory.jsonl",
                [
                    {
                        "schema_version": "1.0",
                        "run_id": "run_a",
                        "case_name": "tecnopolo",
                        "qa_metrics": {"runoff_continuity_pct": 0.5},
                        "recorded_utc": "2026-05-01T00:00:00Z",
                    }
                ],
            )

            with mock.patch.dict(os.environ, {MEMORY_INFORMED_ENV: "1"}):
                ctx = gather_memory_context(
                    memory_dir=mem_dir, case_name="tecnopolo"
                )

            self.assertTrue(ctx.is_empty())
            self.assertEqual(ctx.parametric_hits, [])
            self.assertEqual(ctx.reference_thresholds, {})
            self.assertEqual(ctx.summary, "")
            self.assertTrue(ctx.provenance.get("disabled"))
            self.assertEqual(
                ctx.provenance.get("disabled_reason"),
                "AISWMM_DISABLE_MEMORY_INFORMED",
            )

    def test_enabled_default_returns_populated_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mem_dir = Path(tmp) / "modeling-memory"
            mem_dir.mkdir(parents=True)
            _write_jsonl(
                mem_dir / "parametric_memory.jsonl",
                [
                    {
                        "schema_version": "1.0",
                        "run_id": "run_a",
                        "case_name": "tecnopolo",
                        "qa_metrics": {"runoff_continuity_pct": 0.5},
                        "recorded_utc": "2026-05-01T00:00:00Z",
                    }
                ],
            )

            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(MEMORY_INFORMED_ENV, None)
                ctx = gather_memory_context(
                    memory_dir=mem_dir, case_name="tecnopolo"
                )

            self.assertEqual(ctx.parametric_hit_count, 1)
            self.assertFalse(ctx.provenance.get("disabled"))


if __name__ == "__main__":
    unittest.main()
