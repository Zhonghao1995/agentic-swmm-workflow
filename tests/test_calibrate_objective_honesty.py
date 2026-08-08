"""Regression tests: ``aiswmm calibrate`` stops discarding flags silently.

Bug (found 2026-08-08 CLI help-vs-behavior sweep):

* ``--objective {nse,kge,rmse}`` claimed a free choice with "default
  nse", but the default engine (real SCE-UA) hardcodes KGE
  (``PRIMARY_OBJECTIVE_NAME = "kge"`` in the skill script) and
  ``RealCalibrationConfig`` has no objective field at all — the flag
  was silently discarded and the user got a KGE-optimized calibration
  whatever they asked for, with no trace in the summary.
* Nine real-engine-only flags (``--node``, ``--seed``, ``--ngs``,
  windowing and CSV-column flags) were silently discarded by the
  synthetic walker.

Fix under test: ``--objective`` defaults to None so an explicit pass is
detectable; the real engine rejects nse/rmse with an actionable error
and accepts kge/None; the real summary states ``"objective": "kge"``;
the synthetic path emits one warning line naming ignored real-only
flags and keeps its historic nse default.
"""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.cli import main as cli_main


def _base(run_dir: Path) -> tuple[Path, Path]:
    inp = run_dir / "model.inp"
    inp.write_text("[TITLE]\nstub\n", encoding="utf-8")
    obs = run_dir / "obs.csv"
    obs.write_text("t,q\n", encoding="utf-8")
    return inp, obs


class RealEngineObjectiveGuardTests(unittest.TestCase):
    def _run(self, run_dir: Path, extra: list[str]) -> tuple[int, str]:
        inp, obs = _base(run_dir)
        err = io.StringIO()
        argv = [
            "calibrate",
            "--quiet",
            "--engine",
            "real",
            "--run-id",
            "obj-r1",
            "--total-iters",
            "4",
            "--base-inp",
            str(inp),
            "--observed-csv",
            str(obs),
            "--param",
            "manning_n=0.01,0.03",
            "--run-dir",
            str(run_dir),
            *extra,
        ]
        with mock.patch("sys.stderr", err):
            rc = cli_main(argv)
        return rc, err.getvalue()

    def test_nse_objective_is_rejected_loudly(self) -> None:
        """Pre-fix: accepted, silently discarded, KGE optimized."""
        with TemporaryDirectory() as tmp:
            rc, err = self._run(Path(tmp), ["--objective", "nse"])
        self.assertEqual(rc, 1)
        self.assertIn("not supported by the real SCE-UA engine", err)
        self.assertIn("KGE", err)

    def test_rmse_objective_is_rejected_loudly(self) -> None:
        with TemporaryDirectory() as tmp:
            rc, err = self._run(Path(tmp), ["--objective", "rmse"])
        self.assertEqual(rc, 1)
        self.assertIn("not supported by the real SCE-UA engine", err)

    def test_kge_objective_passes_the_guard(self) -> None:
        """kge matches what the engine does; the run proceeds past the
        objective guard (and fails later only if the patch-map is
        absent, which is the next validation in line)."""
        with TemporaryDirectory() as tmp:
            rc, err = self._run(Path(tmp), ["--objective", "kge"])
        self.assertEqual(rc, 1)
        self.assertNotIn("not supported by the real SCE-UA engine", err)
        self.assertIn("--patch-map", err)

    def test_omitted_objective_passes_the_guard(self) -> None:
        with TemporaryDirectory() as tmp:
            rc, err = self._run(Path(tmp), [])
        self.assertEqual(rc, 1)
        self.assertNotIn("not supported by the real SCE-UA engine", err)
        self.assertIn("--patch-map", err)


class SyntheticEngineIgnoredFlagWarningTests(unittest.TestCase):
    def _run(self, run_dir: Path, extra: list[str]) -> tuple[int, str, str]:
        inp, obs = _base(run_dir)
        out, err = io.StringIO(), io.StringIO()
        argv = [
            "calibrate",
            "--quiet",
            "--engine",
            "synthetic",
            "--run-id",
            "obj-s1",
            "--algorithm",
            "sceua",
            "--total-iters",
            "2",
            "--checkpoint-every",
            "1",
            "--base-inp",
            str(inp),
            "--observed-csv",
            str(obs),
            "--param",
            "manning_n=0.01,0.03",
            "--run-dir",
            str(run_dir),
            *extra,
        ]
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = cli_main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_real_only_flags_warn_once_and_run_completes(self) -> None:
        """Pre-fix: --seed/--node were silently discarded, no signal."""
        with TemporaryDirectory() as tmp:
            rc, out, err = self._run(
                Path(tmp), ["--seed", "7", "--node", "J9"]
            )
        self.assertEqual(rc, 0)
        warnings = [ln for ln in err.splitlines() if ln.startswith("warning:")]
        self.assertEqual(len(warnings), 1)
        self.assertIn("--seed", warnings[0])
        self.assertIn("--node", warnings[0])
        self.assertIn("synthetic", warnings[0])

    def test_no_warning_when_only_synthetic_flags_used(self) -> None:
        with TemporaryDirectory() as tmp:
            rc, out, err = self._run(Path(tmp), ["--objective", "rmse"])
        self.assertEqual(rc, 0)
        self.assertNotIn("warning:", err)

    def test_synthetic_default_objective_stays_nse(self) -> None:
        with TemporaryDirectory() as tmp:
            rc, out, _err = self._run(Path(tmp), [])
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload.get("objective"), "nse")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
