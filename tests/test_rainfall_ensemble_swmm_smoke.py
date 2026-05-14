"""SWMM end-to-end smoke test for rainfall_ensemble.py (issue #51).

Runs a tiny perturbation ensemble against the Todcreek 1-subcatchment
fixture and confirms:
  * the summary JSON exists
  * every realisation patches a copy of the base INP
  * swmm5 actually runs (status="ok") for every realisation
  * the summary's `swmm_ensemble_stats` aggregates peak_flow + total volume

Skipped if `swmm5` is not on PATH.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
RAINFALL_PY = REPO_ROOT / "skills" / "swmm-uncertainty" / "scripts" / "rainfall_ensemble.py"
INP_FIXTURE = REPO_ROOT / "examples" / "todcreek" / "model_chicago5min.inp"


def _has_swmm5() -> bool:
    return shutil.which("swmm5") is not None


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not _has_swmm5(), reason="swmm5 binary not available on PATH")
class RainfallEnsembleSwmmSmoke(unittest.TestCase):
    """End-to-end: 3 realisations run through swmm5 + summary aggregates."""

    @classmethod
    def setUpClass(cls) -> None:
        if not RAINFALL_PY.exists():
            raise unittest.SkipTest("rainfall_ensemble.py not yet present.")
        cls.mod = _load_module("rainfall_ensemble_smoke", RAINFALL_PY)

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="rainfall-ensemble-smoke-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp_root, ignore_errors=True))

        # Extract the observed TS_RAIN series from the fixture INP so the
        # ensemble runs against the same hyetograph the base case uses.
        ts_csv = self.tmp_root / "observed_ts_rain.csv"
        records: list[str] = ["timestamp,rainfall_mm_per_hr"]
        in_ts = False
        for raw in INP_FIXTURE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                in_ts = (line == "[TIMESERIES]")
                continue
            if not in_ts or not line or line.startswith(";"):
                continue
            parts = line.split()
            if len(parts) < 4 or parts[0] != "TS_RAIN":
                continue
            date, time, val = parts[1], parts[2], parts[3]
            # SWMM dates are mm/dd/yyyy
            month, day, year = date.split("/")
            ts = f"{int(year):04d}-{int(month):02d}-{int(day):02d} {time}:00"
            records.append(f"{ts},{val}")
        ts_csv.write_text("\n".join(records) + "\n", encoding="utf-8")
        self.observed_csv = ts_csv

    def test_perturbation_ensemble_runs_swmm(self) -> None:
        cfg = {
            "method": "perturbation",
            "perturbation": {
                "model": "multiplicative",
                "sigma": 0.20,
                "preserve_total_volume": False,
            },
            "n_realisations": 3,
            "input_rainfall_path": str(self.observed_csv),
        }
        payload = self.mod.run_ensemble(
            method="perturbation",
            config=cfg,
            run_root=self.tmp_root,
            base_inp=INP_FIXTURE,
            series_name="TS_RAIN",
            swmm_node="O1",
            seed=42,
            dry_run=False,
        )
        summary_path = self.tmp_root / "09_audit" / "rainfall_ensemble_summary.json"
        self.assertTrue(summary_path.exists())
        on_disk = json.loads(summary_path.read_text())
        self.assertEqual(on_disk["method"], "perturbation")
        self.assertEqual(on_disk["n_realisations"], 3)
        # Every realisation ran swmm5 successfully
        volumes: list[float] = []
        for rec in payload["realisations"]:
            self.assertEqual(
                rec.get("swmm", {}).get("status"),
                "ok",
                msg=f"realisation {rec['name']} did not finish: {rec.get('swmm')}",
            )
            peak = rec["swmm"]["metrics"]["peak_flow"]
            vol = rec["swmm"]["metrics"]["total_volume_m3"]
            self.assertIsNotNone(peak, msg=f"no peak_flow for {rec['name']}")
            self.assertIsNotNone(vol, msg=f"no total_volume for {rec['name']}")
            self.assertGreater(float(peak), 0.0)
            self.assertGreater(float(vol), 0.0)
            volumes.append(float(vol))
        # The ensemble summary aggregates peak + volume
        stats = payload["swmm_ensemble_stats"]
        self.assertEqual(stats["peak_flow"]["count"], 3)
        self.assertEqual(stats["total_volume_m3"]["count"], 3)
        # Different rainfall -> different SWMM volume (perturbation works)
        self.assertGreater(max(volumes) - min(volumes), 0.0, msg=f"volumes identical: {volumes}")


if __name__ == "__main__":
    unittest.main()
