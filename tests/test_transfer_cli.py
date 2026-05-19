"""Tests for ``aiswmm transfer`` CLI surface (PRD-07 Phase 5).

Smokes the CLI end-to-end so the registration in
``agentic_swmm.cli`` stays wired and the JSON shape is stable. Heavy
recommender logic is exercised in ``test_cross_watershed_transfer``;
these tests only confirm the shell-facing contract.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser
from agentic_swmm.memory.calibration_memory import (
    CalibrationRecord,
    record_calibration_run,
)


_TINY_INP = """\
[OPTIONS]
FLOW_UNITS CMS

[SUBCATCHMENTS]
S1               RG1              J1               10.0     50.0     800.0    2.5      0
S2               RG1              J1               5.0      30.0     400.0    1.5      0

[CONDUITS]
C1               J1               O1               1000       0.013

[OUTFALLS]
O1               90         FREE
"""


def _seed_store(store: Path, case_name: str) -> None:
    record = CalibrationRecord(
        run_id="r1",
        case_name=case_name,
        algorithm="sceua",
        parameters={"manning_n": 0.013, "imdmax": 0.25},
        objective_name="NSE",
        objective_value=0.78,
        swmm5_version="5.2.4",
        created_at="2026-05-19T00:00:00Z",
    )
    record_calibration_run(store, record)


class TransferCliSmokeTests(unittest.TestCase):
    """End-to-end smokes for ``aiswmm transfer``."""

    def _run(self, *argv: str) -> tuple[int, str]:
        parser = build_parser()
        args = parser.parse_args(["transfer", *argv])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = int(args.func(args) or 0)
        return rc, buf.getvalue()

    def test_json_mode_returns_valid_json_with_recommendations(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "new.inp"
            inp.write_text(_TINY_INP, encoding="utf-8")
            store = tmp / "calibration_memory.jsonl"
            _seed_store(store, "twin")
            # Put a 'twin.inp' under cases/twin/ for the lookup.
            cases_dir = tmp / "cases" / "twin"
            cases_dir.mkdir(parents=True, exist_ok=True)
            (cases_dir / "twin.inp").write_text(_TINY_INP, encoding="utf-8")

            rc, out = self._run(
                "--inp", str(inp),
                "--calibration-store", str(store),
                "--repo-root", str(tmp),
                "--top-k", "3",
                "--json",
            )

        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        # Top-level shape checks.
        self.assertEqual(payload["target_inp"], str(inp))
        self.assertEqual(payload["top_k"], 3)
        # One recommendation lands.
        self.assertEqual(len(payload["recommendations"]), 1, payload)
        rec = payload["recommendations"][0]
        self.assertEqual(rec["source_case"], "twin")
        self.assertEqual(rec["confidence"], "memory_informed")
        self.assertEqual(rec["proposed_parameters"], {"manning_n": 0.013, "imdmax": 0.25})

    def test_json_mode_returns_empty_recommendations_when_store_empty(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "new.inp"
            inp.write_text(_TINY_INP, encoding="utf-8")
            store = tmp / "calibration_memory.jsonl"  # not created

            rc, out = self._run(
                "--inp", str(inp),
                "--calibration-store", str(store),
                "--repo-root", str(tmp),
                "--json",
            )

        self.assertEqual(rc, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["recommendations"], [])

    def test_table_mode_emits_human_readable_lines(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "new.inp"
            inp.write_text(_TINY_INP, encoding="utf-8")
            store = tmp / "calibration_memory.jsonl"
            _seed_store(store, "twin")
            cases_dir = tmp / "cases" / "twin"
            cases_dir.mkdir(parents=True, exist_ok=True)
            (cases_dir / "twin.inp").write_text(_TINY_INP, encoding="utf-8")

            rc, out = self._run(
                "--inp", str(inp),
                "--calibration-store", str(store),
                "--repo-root", str(tmp),
                "--top-k", "1",
            )

        self.assertEqual(rc, 0)
        # Header row landed.
        self.assertIn("source_case", out)
        self.assertIn("similarity", out)
        self.assertIn("twin", out)

    def test_missing_inp_returns_nonzero_exit(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp = tmp / "ghost.inp"  # not created
            store = tmp / "calibration_memory.jsonl"
            rc, out = self._run(
                "--inp", str(inp),
                "--calibration-store", str(store),
                "--repo-root", str(tmp),
                "--json",
            )
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
