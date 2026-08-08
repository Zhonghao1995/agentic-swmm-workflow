"""Regression tests: the two remaining naive-timestamp holes (2026-08-08 sweep).

1. ``swmm_runner.py`` stamped its manifest ``created_at`` with naive
   local time while every sibling manifest writer stamps UTC-aware
   ISO 8601. Cross-machine comparisons of run manifests silently
   shifted by the machine's UTC offset.
2. ``user_baseline._within_lookback`` parsed timestamps but caught only
   ``ValueError``: an aware-vs-naive comparison raises ``TypeError``,
   which escaped the permissive filter and crashed
   ``compute_user_baseline`` on any historical row stamped with a
   naive ISO string that was lexicographically older than the cutoff.
   Naive stamps now default to UTC (what every sibling store writes)
   and ``TypeError`` joins the defensive except.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from agentic_swmm.memory.user_baseline import _within_lookback


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = REPO_ROOT / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "_swmm_runner_tz_test", RUNNER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RunnerManifestCreatedAtTests(unittest.TestCase):
    def test_created_at_is_utc_aware(self) -> None:
        runner = _load_runner_module()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inp = tmp_path / "model.inp"
            inp.write_text("[TITLE]\ntest\n", encoding="utf-8")
            run_dir = tmp_path / "run"

            def fake(inp_, rpt, out, stdout_path, stderr_path, timeout=600.0):
                rpt.write_text("  EPA SWMM 5.2 (Build 5.2.4)\n", encoding="utf-8")
                out.write_bytes(b"")
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text("", encoding="utf-8")
                return 0

            runner.run_swmm = fake
            runner.get_swmm5_version = lambda: runner.EXPECTED_SWMM_VERSION
            args = types.SimpleNamespace(
                inp=inp,
                run_dir=run_dir,
                node="O1",
                rpt_name=None,
                out_name=None,
                timeout=600.0,
                gate=False,
            )
            runner.cmd_run(args)
            manifest = json.loads(
                (run_dir / "manifest.json").read_text(encoding="utf-8")
            )

        stamped = datetime.fromisoformat(manifest["created_at"])
        self.assertIsNotNone(stamped.tzinfo)
        self.assertEqual(stamped.utcoffset(), timedelta(0))


class WithinLookbackNaiveTests(unittest.TestCase):
    def test_naive_old_row_excludes_without_raising(self) -> None:
        """Pre-fix: aware-vs-naive comparison raised TypeError, which
        escaped the ValueError-only except and crashed the caller."""
        row = {"recorded_utc": "2026-06-30T00:00:00"}
        self.assertFalse(
            _within_lookback(row, cutoff_iso="2026-07-01T00:00:00Z")
        )

    def test_naive_stamp_is_read_as_utc(self) -> None:
        # Chronologically after the cutoff but lexicographically before
        # it (shape difference forces the parse path).
        row = {"recorded_utc": "2026-07-01T23:30:00"}
        self.assertTrue(
            _within_lookback(row, cutoff_iso="2026-07-01T23:00:00+00:00")
        )

    def test_same_instant_different_shapes_still_passes(self) -> None:
        row = {"recorded_utc": "2026-07-01T23:00:00+00:00"}
        self.assertTrue(
            _within_lookback(row, cutoff_iso="2026-07-01T23:00:00Z")
        )

    def test_unparsable_timestamp_stays_permissive(self) -> None:
        row = {"recorded_utc": "not-a-date"}
        self.assertTrue(
            _within_lookback(row, cutoff_iso="2026-07-01T00:00:00Z")
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
