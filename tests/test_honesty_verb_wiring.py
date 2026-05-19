"""Verb-level wiring tests for the honesty layer (PRD-08 Phase A.1).

These tests exercise the public CLI surfaces (``calibrate``, ``storm``,
``compare``) through ``argparse.Namespace`` synthesis rather than
subprocess so they run in milliseconds. The ``run`` verb is exercised
in :mod:`tests.test_postflight_swmm_error` against a synthetic .rpt
file because the real runner shells out to swmm5 — out of scope here.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.honesty import HONESTY_DISABLE_ENV
from agentic_swmm.commands import calibrate as calibrate_cmd
from agentic_swmm.commands import compare as compare_cmd
from agentic_swmm.commands import storm as storm_cmd


def _calibrate_namespace(**overrides) -> argparse.Namespace:
    # PRD-08 A.2: the canonical attribute is ``inp``; ``base_inp`` is
    # an alias on the CLI surface but the namespace destination is
    # unified. Tests now pass ``inp=`` (or via the back-compat shim
    # below, ``base_inp=``).
    base = {
        "run_id": "test",
        "algorithm": "sceua",
        "total_iters": 1,
        "checkpoint_every": 1,
        "inp": Path("/nonexistent/base.inp"),
        "observed_csv": None,
        "param": ["a=0,1"],
        "objective": "nse",
        "run_dir": Path("/tmp/test_run_dir"),
        "progress": False,
        "print_every": 1,
        "quiet": False,
    }
    # Test shim: a caller that still passes ``base_inp=`` overrides
    # ``inp`` so existing test bodies do not have to be rewritten in
    # bulk.
    if "base_inp" in overrides:
        overrides["inp"] = overrides.pop("base_inp")
    base.update(overrides)
    return argparse.Namespace(**base)


def _storm_namespace(**overrides) -> argparse.Namespace:
    base = {
        "depth_mm": 25.0,
        "duration_min": 60,
        "shape": None,
        "interval_min": 5,
        "start_time": "2000-01-01 00:00",
        "station_id": "STN1",
        "peak_position": 0.5,
        "idf": None,
        "quartile": None,
        "from_library": None,
        "storm_library": None,
        "out": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _compare_namespace(**overrides) -> argparse.Namespace:
    base = {
        "run_a": Path("/nonexistent/run_a"),
        "run_b": Path("/nonexistent/run_b"),
        "metrics": None,
        "benchmarks_path": None,
        "json": False,
        "per_node": False,
        "per_subcatch": False,
        "override_version": False,
        "parametric_store": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# calibrate
# ---------------------------------------------------------------------------


class CalibrateWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_stub_banner_appears_on_stdout_when_not_quiet(self) -> None:
        inp = self.tmp / "base.inp"
        inp.write_text("[TITLE]\nstub\n", encoding="utf-8")
        ns = _calibrate_namespace(
            base_inp=inp,
            run_dir=self.tmp / "out",
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = calibrate_cmd.main(ns)
        self.assertEqual(rc, 0)
        self.assertIn("synthetic walker", stdout.getvalue())
        self.assertIn("not a calibration", stdout.getvalue())

    def test_quiet_suppresses_banner(self) -> None:
        inp = self.tmp / "base.inp"
        inp.write_text("[TITLE]\nstub\n", encoding="utf-8")
        ns = _calibrate_namespace(
            base_inp=inp,
            run_dir=self.tmp / "out",
            quiet=True,
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = calibrate_cmd.main(ns)
        self.assertEqual(rc, 0)
        self.assertNotIn("synthetic walker", stdout.getvalue())

    def test_missing_base_inp_exits_2_with_stderr(self) -> None:
        ns = _calibrate_namespace(
            base_inp=self.tmp / "does-not-exist.inp",
            run_dir=self.tmp / "out",
            quiet=True,
        )
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as cm:
                calibrate_cmd.main(ns)
        self.assertEqual(cm.exception.code, 2)
        # PRD-08 A.2: the canonical flag is ``--inp``; the
        # fail-fast error mirrors the canonical name.
        self.assertIn("error: --inp does not exist:", stderr.getvalue())


# ---------------------------------------------------------------------------
# storm
# ---------------------------------------------------------------------------


class StormWiringTests(unittest.TestCase):
    def test_omitting_shape_emits_default_notice(self) -> None:
        ns = _storm_namespace(shape=None)
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            rc = storm_cmd.main(ns)
        self.assertEqual(rc, 0)
        self.assertIn(
            "note: --shape not supplied, using uniform",
            stderr.getvalue(),
        )

    def test_explicit_shape_does_not_emit_notice(self) -> None:
        ns = _storm_namespace(shape="uniform")
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            rc = storm_cmd.main(ns)
        self.assertEqual(rc, 0)
        self.assertNotIn("note: --shape not supplied", stderr.getvalue())

    def test_chicago_idf_overrides_depth_mm_emits_override_warning(self) -> None:
        ns = _storm_namespace(
            shape="chicago",
            idf="a=1000,b=10,c=0.85",
            depth_mm=25.0,
            duration_min=60,
        )
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            rc = storm_cmd.main(ns)
        self.assertEqual(rc, 0)
        self.assertIn(
            "warning: --depth-mm 25.0 ignored because --idf is set",
            stderr.getvalue(),
        )


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


class CompareWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_missing_run_a_exits_2_before_rendering_table(self) -> None:
        existing = self.tmp / "run_b"
        existing.mkdir()
        ns = _compare_namespace(
            run_a=self.tmp / "nope",
            run_b=existing,
        )
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as cm:
                compare_cmd.main(ns)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("error: --run-a does not exist:", stderr.getvalue())
        # No comparison table should render on the fail-fast path.
        self.assertNotIn("verdict", stdout.getvalue())

    def test_missing_run_b_exits_2(self) -> None:
        existing = self.tmp / "run_a"
        existing.mkdir()
        ns = _compare_namespace(
            run_a=existing,
            run_b=self.tmp / "nope",
        )
        stderr = io.StringIO()
        stdout = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as cm:
                compare_cmd.main(ns)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("error: --run-b does not exist:", stderr.getvalue())


# ---------------------------------------------------------------------------
# Postflight rpt scan integration
# ---------------------------------------------------------------------------


class PostflightSwmmErrorTests(unittest.TestCase):
    """``postflight_qa`` must surface verbatim SWMM errors as a FAIL row."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_rpt_with_swmm_error_produces_fail_status(self) -> None:
        from agentic_swmm.agent.swmm_runtime.postflight import postflight_qa

        run_dir = self.tmp / "run"
        run_dir.mkdir()
        rpt = run_dir / "model.rpt"
        rpt.write_text(
            "  EPA SWMM 5.2\n"
            "  ERROR 205: invalid keyword at line 5\n",
            encoding="utf-8",
        )
        report = postflight_qa(run_dir)
        self.assertEqual(report.status, "FAIL")
        codes = {failure["code"] for failure in report.failures}
        self.assertIn("swmm_solver_error", codes)
        # The detail must be the verbatim SWMM error line.
        detail = next(
            f["detail"] for f in report.failures if f["code"] == "swmm_solver_error"
        )
        self.assertEqual(detail, "ERROR 205: invalid keyword at line 5")

    def test_clean_rpt_does_not_add_swmm_solver_error_row(self) -> None:
        from agentic_swmm.agent.swmm_runtime.postflight import postflight_qa

        run_dir = self.tmp / "run"
        run_dir.mkdir()
        rpt = run_dir / "model.rpt"
        rpt.write_text(
            "  EPA SWMM 5.2\n"
            "  Runoff Quantity Continuity\n"
            "  Continuity Error (%) .....     -0.171\n",
            encoding="utf-8",
        )
        report = postflight_qa(run_dir)
        codes = {failure["code"] for failure in report.failures}
        self.assertNotIn("swmm_solver_error", codes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
