"""Phase D — the calibrate JSON summary flags itself as a synthetic stub.

The ``aiswmm calibrate`` CLI verb is a synthetic walker, not the real
SCE-UA / DREAM-ZS solver (which lives in
``skills/swmm-calibration/scripts/swmm_calibrate.py``). A human is warned by
the STUB_BANNER, but a machine/agent parsing the JSON summary saw only
``best_objective`` with no synthetic flag. This pins ``is_stub`` /
``engine`` into the machine-readable summary so the dishonesty is closed on
both surfaces.

(Wiring the CLI verb to the real spotpy-based solver is a separate,
domain-heavy feature — out of scope here.)
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _namespace(inp: Path, run_dir: Path, **overrides) -> argparse.Namespace:
    base = {
        "run_id": "test",
        "algorithm": "sceua",
        "total_iters": 1,
        "checkpoint_every": 1,
        "inp": inp,
        "observed_csv": None,
        "param": ["a=0,1"],
        "objective": "nse",
        "run_dir": run_dir,
        "progress": False,
        "print_every": 1,
        "quiet": True,  # suppress the banner so stdout is pure JSON
        # ADR-0005: the default engine is now the real SCE-UA; these
        # tests pin the SYNTHETIC walker's honesty contract explicitly.
        "engine": "synthetic",
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class CalibrateSummaryHonestyTests(unittest.TestCase):
    def test_summary_json_flags_synthetic_stub(self) -> None:
        from agentic_swmm.commands import calibrate as calibrate_cmd

        with TemporaryDirectory() as tmp:
            inp = Path(tmp) / "base.inp"
            inp.write_text("[TITLE]\ncal\n", encoding="utf-8")
            run_dir = Path(tmp) / "run"
            ns = _namespace(inp, run_dir)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = calibrate_cmd.main(ns)
            self.assertEqual(rc, 0)

            summary = json.loads(out.getvalue())
            self.assertTrue(summary["is_stub"])
            self.assertEqual(summary["engine"], "synthetic_walker")
            # the existing fields are still present
            self.assertIn("best_objective", summary)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
