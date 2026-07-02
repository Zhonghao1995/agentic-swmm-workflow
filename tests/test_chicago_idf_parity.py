"""Numeric parity: the two Chicago-from-IDF implementations stay equal.

The Keifer–Chu math deliberately exists twice:

- ``agentic_swmm/agent/swmm_runtime/design_storm.py::_chicago_from_idf``
  — in-process, serves ``aiswmm storm --idf`` and the
  ``generate_storm_shape`` tool.
- ``skills/swmm-climate/scripts/design_storm.py::chicago_hyetograph``
  — stdlib-only script whose documented portability constraint forbids
  importing ``agentic_swmm``, so the code cannot be shared.

This suite is the lock that makes the duplication safe: per-bin depths
identical, storm total exactly the IDF depth of the full duration, no
interior dry bins (regression: aligned peak positions used to produce a
zero-depth block right at the peak), and a unimodal shape.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from agentic_swmm.agent.swmm_runtime.design_storm import _chicago_from_idf

_SKILLS_SCRIPT = (
    Path(__file__).parent.parent
    / "skills"
    / "swmm-climate"
    / "scripts"
    / "design_storm.py"
)


def _load_skills_module():
    spec = importlib.util.spec_from_file_location(
        "swmm_climate_design_storm", _SKILLS_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_SKILLS = _load_skills_module()

# (a, b, c) IDF coefficient sets: a synthetic mid-range curve and the
# fitted-curve shape used elsewhere in the design-storm tests.
_IDF_SETS = ((800.0, 10.0, 0.7), (65.4, 0.08, 0.81))
# (duration_min, interval_min, r) including the aligned peak positions
# (0.375, 0.5) that used to trigger the dry-bin defect.
_GRIDS = (
    (120, 5, 0.375),
    (120, 5, 0.4),
    (120, 5, 0.5),
    (60, 5, 0.25),
    (180, 10, 0.3),
    (90, 15, 0.6),
)


def _idf_depth(a: float, b: float, c: float, tau: float) -> float:
    """Closed-form IDF cumulative depth D(tau) in mm (tau in minutes)."""
    return (a / ((tau + b) ** c)) * tau / 60.0


def _both(a: float, b: float, c: float, duration: int, dt: int, r: float):
    """Return (skills_depths, runtime_depths) in mm per bin."""
    skills_depths = _SKILLS.chicago_hyetograph(
        coefficients={"a": a, "b": b, "c": c},
        form="generic",
        return_period_yr=2.0,
        duration_min=float(duration),
        dt_min=float(dt),
        r=r,
    )
    intensities = _chicago_from_idf(
        a=a, b=b, c=c, duration_min=duration, peak_position=r, interval_min=dt
    )
    runtime_depths = [i * dt / 60.0 for i in intensities]
    return skills_depths, runtime_depths


class ChicagoIdfParityTests(unittest.TestCase):
    def test_per_bin_depths_identical(self) -> None:
        for a, b, c in _IDF_SETS:
            for duration, dt, r in _GRIDS:
                with self.subTest(idf=(a, b, c), grid=(duration, dt, r)):
                    skills_depths, runtime_depths = _both(a, b, c, duration, dt, r)
                    self.assertEqual(len(skills_depths), len(runtime_depths))
                    for k, (ds, dr) in enumerate(
                        zip(skills_depths, runtime_depths)
                    ):
                        self.assertAlmostEqual(
                            ds, dr, places=9, msg=f"bin {k} diverged"
                        )

    def test_storm_total_is_exact_idf_depth(self) -> None:
        for a, b, c in _IDF_SETS:
            for duration, dt, r in _GRIDS:
                with self.subTest(idf=(a, b, c), grid=(duration, dt, r)):
                    skills_depths, runtime_depths = _both(a, b, c, duration, dt, r)
                    target = _idf_depth(a, b, c, float(duration))
                    self.assertAlmostEqual(sum(skills_depths), target, places=9)
                    self.assertAlmostEqual(sum(runtime_depths), target, places=9)

    def test_no_interior_dry_bin(self) -> None:
        """Regression: aligned peak positions (r*duration on a bin edge)
        used to yield a zero-depth block right next to the peak."""
        for r in (0.375, 0.5):
            with self.subTest(r=r):
                _, runtime_depths = _both(800.0, 10.0, 0.7, 120, 5, r)
                self.assertTrue(
                    all(d > 0.0 for d in runtime_depths),
                    f"dry bin at index {runtime_depths.index(0.0) if 0.0 in runtime_depths else '?'}",
                )

    def test_unimodal_shape(self) -> None:
        """Depths rise monotonically to the peak block and fall after it."""
        for duration, dt, r in _GRIDS:
            with self.subTest(grid=(duration, dt, r)):
                _, depths = _both(800.0, 10.0, 0.7, duration, dt, r)
                peak_idx = depths.index(max(depths))
                for i in range(1, peak_idx + 1):
                    self.assertGreaterEqual(depths[i], depths[i - 1] - 1e-9)
                for i in range(peak_idx + 1, len(depths)):
                    self.assertLessEqual(depths[i], depths[i - 1] + 1e-9)

    def test_keifer_chu_window_property(self) -> None:
        """The peak-centred window of duration tau accumulates ~D(tau).

        Exact in the continuum; discrete bins smear sub-bin structure, so
        the bound loosens as tau approaches a single bin. This documents
        the defining property the exactness tests above rely on.
        """
        a, b, c = 800.0, 10.0, 0.7
        duration, dt, r = 120, 5, 0.4
        _, depths = _both(a, b, c, duration, dt, r)
        peak_t = r * duration
        for tau, rel_tol in ((30.0, 0.06), (60.0, 0.03), (120.0, 1e-9)):
            with self.subTest(tau=tau):
                lo, hi = peak_t - r * tau, peak_t + (1 - r) * tau
                acc = 0.0
                for k, d in enumerate(depths):
                    b0, b1 = k * dt, (k + 1) * dt
                    overlap = max(0.0, min(float(b1), hi) - max(float(b0), lo))
                    acc += d * (overlap / dt)
                target = _idf_depth(a, b, c, tau)
                self.assertAlmostEqual(
                    acc / target, 1.0, delta=rel_tol, msg=f"tau={tau}"
                )


if __name__ == "__main__":
    unittest.main()
