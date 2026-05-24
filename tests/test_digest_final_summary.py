"""PRD-185: final summary block printed at session end.

When the interactive shell ends a turn that produced a SWMM run, the
digest renderer prints a 3-4 line block::

    -------------------------
    Peak: 0.061 CMS @ 03:15 at OUT_0
    Continuity: runoff -0.13 %, routing -0.004 %
    Run dir: runs/2026-05-22/230510_tecnopolo_run

Source of fields:
* ``manifest.json`` in the run dir (PRD-183 ``Run Results`` section).
* ``run_dir`` value taken from the path itself.

When the session produced no SWMM run, ``render_final_summary``
returns the empty string so the runtime can ``if block: print(block)``
without further branching.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.digest_render import render_final_summary


def _write_manifest(run_dir: Path, payload: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


_MANIFEST_OK = {
    "status": "PASS",
    "peak_flow_at_outfall": {"node": "OUT_0", "value": 0.061, "time": "03:15"},
    "continuity_error": {"runoff": -0.13, "routing": -0.004},
}


class FinalSummaryTests(unittest.TestCase):
    def test_session_with_one_swmm_run_renders_three_data_lines(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "230510_tecnopolo_run"
            _write_manifest(run_dir, _MANIFEST_OK)
            block = render_final_summary([run_dir])
        lines = block.splitlines()
        # The first line is the visual separator.
        self.assertTrue(lines[0].startswith("-") or lines[0].startswith("─"))
        # The remaining lines carry peak, continuity, run dir — in
        # that order.
        self.assertIn("Peak: 0.061 CMS @ 03:15 at OUT_0", block)
        self.assertIn(
            "Continuity: runoff -0.13 %, routing -0.004 %", block
        )
        self.assertIn("Run dir: ", block)
        self.assertIn(str(run_dir), block)

    def test_no_swmm_runs_returns_empty_string(self) -> None:
        block = render_final_summary([])
        self.assertEqual(block, "")

    def test_run_dir_without_manifest_is_skipped(self) -> None:
        # Chat-only runs (no manifest.json) must NOT contribute to the
        # summary block; the PRD says the block omits when no SWMM
        # run was generated.
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "230510_chat_only"
            run_dir.mkdir(parents=True)
            block = render_final_summary([run_dir])
        self.assertEqual(block, "")

    def test_multiple_runs_render_each(self) -> None:
        with TemporaryDirectory() as tmp:
            r1 = Path(tmp) / "230510_a"
            r2 = Path(tmp) / "231022_b"
            _write_manifest(r1, _MANIFEST_OK)
            _write_manifest(
                r2,
                {
                    "status": "PASS",
                    "peak_flow_at_outfall": {
                        "node": "OUT_1",
                        # 0.09 (not 0.090) — JSON / Python repr strips
                        # the trailing zero on round-trip, so we pin
                        # what callers actually see.
                        "value": 0.09,
                        "time": "04:30",
                    },
                    "continuity_error": {"runoff": 0.5, "routing": 0.1},
                },
            )
            block = render_final_summary([r1, r2])
        self.assertIn(str(r1), block)
        self.assertIn(str(r2), block)
        self.assertIn("Peak: 0.061 CMS @ 03:15 at OUT_0", block)
        self.assertIn("Peak: 0.09 CMS @ 04:30 at OUT_1", block)

    def test_missing_peak_or_continuity_falls_through_gracefully(self) -> None:
        # A partial manifest should still produce a block (so the
        # user sees the run dir at least) but omit missing fields.
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "230510_partial"
            _write_manifest(run_dir, {"status": "PASS"})
            block = render_final_summary([run_dir])
        # Run dir line is always present when the manifest exists.
        self.assertIn("Run dir:", block)
        # No Peak / Continuity lines when the data is missing.
        self.assertNotIn("Peak:", block)
        self.assertNotIn("Continuity:", block)


if __name__ == "__main__":
    unittest.main()
