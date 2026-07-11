"""ADR-0005: the calibrate facade drives the REAL SCE-UA engine.

The real spotpy loop runs in-process with the engine's own injection
seams (``swmm_runner`` / ``extract_series``) replaced by fakes, so no
swmm5 binary is needed and the whole experiment takes milliseconds:

* the fake runner reads the PATCHED value out of each trial's model.inp
  (proving the patch-map -> inp_patch pipeline really ran), and
* the fake extractor returns ``observed * (value / TARGET)``, so KGE is
  maximal exactly at the target value and SCE-UA has a real gradient to
  climb.

What the contract tests pin: checkpoints written through the SAME
``progress.json`` primitive the stub proved, experiment-dir layout from
the grilled design (convergence.csv, calibration_summary.json,
best_params.json, 09_audit/ candidate artifacts, trials/), honesty flip
(``is_stub: false``), patch-map fail-fast, and the same-units magnitude
guard.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from agentic_swmm.agent.swmm_runtime.calibration_runner import (
    RealCalibrationConfig,
    run_real_calibration,
)

TARGET = 0.05
BASE_INP = """[TITLE]
calibration fixture
[SUBAREAS]
S1   0.010   0.10   0.05   25   0
"""
PATCH_MAP = {"n_imperv_s1": {"section": "[SUBAREAS]", "object": "S1", "field_index": 1}}


def _write_fixtures(tmp: Path, *, observed_scale: float = 1.0) -> tuple[Path, Path, Path]:
    inp = tmp / "base.inp"
    inp.write_text(BASE_INP, encoding="utf-8")
    patch_map = tmp / "patch_map.json"
    patch_map.write_text(json.dumps(PATCH_MAP), encoding="utf-8")
    observed = tmp / "observed.csv"
    rows = ["timestamp,flow"]
    for hour in range(24):
        flow = (10.0 + hour % 7) * observed_scale
        rows.append(f"2024-06-01 {hour:02d}:00,{flow}")
    observed.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return inp, patch_map, observed


def _fake_runner(inp: Path, trial_dir: Path):
    """Read the patched S1 value back out of the trial INP (proves the
    patch pipeline ran) and stash it for the extractor."""
    value = None
    for line in inp.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts and parts[0] == "S1":
            value = float(parts[1])
            break
    out = trial_dir / "model.out"
    out.write_text(json.dumps({"value": value}), encoding="utf-8")
    return 0, trial_dir / "model.rpt", out


def _make_extractor(observed_csv: Path):
    obs = pd.read_csv(observed_csv)

    def _extract(out_path: Path) -> pd.DataFrame:
        value = json.loads(Path(out_path).read_text(encoding="utf-8"))["value"]
        scale = value / TARGET
        return pd.DataFrame(
            {"timestamp": pd.to_datetime(obs["timestamp"]), "flow": obs["flow"] * scale}
        )

    return _extract


def _config(inp: Path, observed: Path, patch_map: Path, **overrides) -> RealCalibrationConfig:
    base = dict(
        run_id="calib-test",
        base_inp=inp,
        observed_csv=observed,
        patch_map_path=patch_map,
        parameters=[("n_imperv_s1", 0.01, 0.10)],
        total_iters=30,
        ngs=2,
        seed=7,
        checkpoint_every=2,
    )
    base.update(overrides)
    return RealCalibrationConfig(**base)


class RealEngineExperimentTests(unittest.TestCase):
    def _run(self, tmp: Path, **overrides):
        inp, patch_map, observed = _write_fixtures(tmp)
        cfg = _config(inp, observed, patch_map, **overrides)
        run_dir = tmp / "experiment"
        run_dir.mkdir()
        checkpoints = []
        result = run_real_calibration(
            cfg,
            run_dir,
            progress_callback=checkpoints.append,
            swmm_runner=_fake_runner,
            extract_series=_make_extractor(observed),
        )
        return result, run_dir, checkpoints

    def test_experiment_layout_and_honesty(self) -> None:
        with TemporaryDirectory() as raw:
            result, run_dir, checkpoints = self._run(Path(raw))

            summary = json.loads((run_dir / "calibration_summary.json").read_text())
            self.assertEqual(summary["engine"], "sceua-spotpy")
            self.assertIs(summary["is_stub"], False)
            self.assertTrue((run_dir / "convergence.csv").is_file())
            self.assertTrue((run_dir / "best_params.json").is_file())
            self.assertTrue(
                (run_dir / "09_audit").is_dir(), "candidate artifacts missing"
            )
            trials = list((run_dir / "trials").glob("sceua_*"))
            self.assertGreater(len(trials), 5, "engine trials did not land in trials/")
            self.assertTrue((run_dir / "progress.json").is_file())
            self.assertTrue(checkpoints, "no checkpoints fired")
            self.assertEqual(result.errors, [])
            self.assertEqual(result.warnings, [])

    def test_engine_actually_optimises_toward_target(self) -> None:
        with TemporaryDirectory() as raw:
            result, _, checkpoints = self._run(Path(raw))
            self.assertGreater(result.best_objective, 0.95, "KGE should approach 1")
            best = result.best_parameters["n_imperv_s1"]
            self.assertAlmostEqual(best, TARGET, delta=0.02)
            # Checkpoint cadence: every 2nd evaluation, monotone iteration index.
            indices = [c.iter_index for c in checkpoints]
            self.assertEqual(indices, sorted(indices))
            self.assertTrue(all(i % 2 == 0 for i in indices))

    def test_units_guard_screams_on_magnitude_mismatch(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp, patch_map, observed = _write_fixtures(tmp, observed_scale=1000.0)
            cfg = _config(inp, observed, patch_map)
            run_dir = tmp / "experiment"
            run_dir.mkdir()

            # Extractor built from UNSCALED flows: simulated is ~1000x below
            # observed, the classic L/s-vs-m3/s foot-gun.
            unscaled = tmp / "unscaled.csv"
            _write_fixtures(tmp)  # rewrites fixtures at scale 1.0
            unscaled_extract = _make_extractor(tmp / "observed.csv")
            # Recreate the big-observed file the engine will score against.
            _write_fixtures(tmp, observed_scale=1000.0)

            result = run_real_calibration(
                cfg,
                run_dir,
                swmm_runner=_fake_runner,
                extract_series=unscaled_extract,
            )
            self.assertTrue(result.warnings, "magnitude guard did not fire")
            self.assertIn("UNITS MISMATCH", result.warnings[0])
            summary = json.loads((run_dir / "calibration_summary.json").read_text())
            self.assertIn("warnings", summary)

    def test_unknown_param_fails_fast_against_patch_map(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            inp, patch_map, observed = _write_fixtures(tmp)
            cfg = _config(
                inp, observed, patch_map, parameters=[("ghost_param", 0.0, 1.0)]
            )
            with self.assertRaises(ValueError) as ctx:
                run_real_calibration(cfg, tmp / "x", swmm_runner=_fake_runner)
            self.assertIn("ghost_param", str(ctx.exception))
            self.assertIn("n_imperv_s1", str(ctx.exception))  # lists available


class RealEngineCliValidationTests(unittest.TestCase):
    """The verb's real-mode gate: actionable errors before anything runs."""

    def _ns(self, tmp: Path, **overrides):
        import argparse

        inp = tmp / "base.inp"
        inp.write_text(BASE_INP, encoding="utf-8")
        base = dict(
            run_id="t",
            algorithm="sceua",
            total_iters=5,
            checkpoint_every=1,
            inp=inp,
            observed_csv=None,
            engine="real",
            patch_map=None,
            node="O1",
            attr="Total_inflow",
            aggregate="none",
            obs_start=None,
            obs_end=None,
            timestamp_col=None,
            flow_col=None,
            seed=1,
            ngs=2,
            param=["n_imperv_s1=0.01,0.1"],
            objective="kge",
            run_dir=tmp / "run",
            progress=False,
            print_every=1,
            quiet=True,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_real_mode_requires_observed_and_patch_map(self) -> None:
        from agentic_swmm.commands import calibrate as calibrate_cmd

        with TemporaryDirectory() as raw:
            rc = calibrate_cmd.main(self._ns(Path(raw)))
        self.assertEqual(rc, 1)

    def test_dream_zs_is_rejected_with_pointer(self) -> None:
        from agentic_swmm.commands import calibrate as calibrate_cmd

        with TemporaryDirectory() as raw:
            rc = calibrate_cmd.main(self._ns(Path(raw), algorithm="dream-zs"))
        self.assertEqual(rc, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
