"""Calibration must never score a stale or errored SWMM run (review P1-10).

``run_swmm`` reuses deterministic trial directories (e.g. ``cmd_validate``'s
fixed ``validation`` trial name), and real swmm5 can exit 0 while writing
``ERROR <digits>:`` lines into the ``.rpt`` and leaving no usable ``.out``.
Before the fix, a failed run in a reused directory left the previous trial's
``.out`` in place and the evaluator scored it, so an invalid parameter set
could inherit a good score. These tests pin the two honesty guarantees in
``run_swmm`` without requiring the swmm5 binary (the subprocess call is faked).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "skills" / "swmm-calibration" / "scripts"
CALIBRATE_PY = SCRIPTS_DIR / "swmm_calibrate.py"


def _load_calibrate():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("swmm_calibrate_honesty_mod", CALIBRATE_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_swmm(rpt_body: str, out_body: str | None):
    """Return a subprocess.run stand-in that writes given rpt/out and exits 0."""

    def _run(cmd, *args, **kwargs):
        rpt_path = Path(cmd[2])
        out_path = Path(cmd[3])
        rpt_path.write_text(rpt_body, encoding="utf-8")
        if out_body is not None:
            out_path.write_text(out_body, encoding="utf-8")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    return _run


class RunSwmmHonestyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_calibrate()
        self._orig_run = self.mod.subprocess.run
        self.tmp = Path(self._make_tmp())

    def tearDown(self) -> None:
        self.mod.subprocess.run = self._orig_run

    def _make_tmp(self) -> str:
        import tempfile

        d = tempfile.mkdtemp(prefix="calib-honesty-")
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return d

    def test_stale_out_is_removed_before_an_errored_run(self) -> None:
        run_dir = self.tmp / "validation"
        run_dir.mkdir(parents=True)
        stale = run_dir / "model.out"
        stale.write_text("STALE-GOOD-RESULT", encoding="utf-8")
        # swmm5 exits 0 but writes ERROR lines and produces no fresh .out.
        self.mod.subprocess.run = _fake_swmm("ERROR 111: invalid conduit\n", out_body=None)

        rc, rpt, out = self.mod.run_swmm(self.tmp / "model.inp", run_dir)

        self.assertFalse(out.exists(), "stale .out must not survive an errored run")
        self.assertTrue(self.mod.rpt_error_lines(rpt), "rpt errors should be detected")

    def test_out_is_dropped_when_rc0_but_rpt_has_errors(self) -> None:
        run_dir = self.tmp / "validation"
        run_dir.mkdir(parents=True)
        # swmm5 exits 0, writes ERROR lines AND a bogus .out.
        self.mod.subprocess.run = _fake_swmm("ERROR 138: node has no outlet\n", out_body="BOGUS")

        rc, rpt, out = self.mod.run_swmm(self.tmp / "model.inp", run_dir)

        self.assertEqual(rc, 0)
        self.assertFalse(out.exists(), "an errored run's .out must be dropped, not scored")

    def test_clean_run_keeps_its_output(self) -> None:
        run_dir = self.tmp / "trial"
        run_dir.mkdir(parents=True)
        self.mod.subprocess.run = _fake_swmm("Analysis begun...\n No errors.\n", out_body="GOOD")

        rc, rpt, out = self.mod.run_swmm(self.tmp / "model.inp", run_dir)

        self.assertEqual(rc, 0)
        self.assertTrue(out.exists())
        self.assertEqual(out.read_text(encoding="utf-8"), "GOOD")
        self.assertEqual(self.mod.rpt_error_lines(rpt), [])


if __name__ == "__main__":
    unittest.main()
