"""Tests for SWMM solver-version compatibility gating in ``compare_runs``.

The version-compat policy is a guard rail: same-version → compatible,
same-minor different-patch → compatible with advisory, different-minor
or unparseable → incomparable unless the caller passes
``override_version=True``. These tests pin the policy at the module
level and the integration at ``compare_runs`` level.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.swmm_runtime.compare import compare_runs
from agentic_swmm.agent.swmm_runtime.version_compat import (
    SwmmVersionCompatVerdict,
    check_swmm_versions_for_compare,
)


_HEALTHY_RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  **************************        Volume         Depth
  Runoff Quantity Continuity     hectare-m            mm
  **************************     ---------       -------
  Total Precipitation ......         0.092        26.208
  Surface Runoff ...........         0.037        10.536
  Continuity Error (%) .....        -0.171


  **************************        Volume        Volume
  Flow Routing Continuity        hectare-m      10^6 ltr
  **************************     ---------     ---------
  External Outflow .........         0.037         0.369
  Continuity Error (%) .....         0.500
"""


def _write_run(parent: Path, name: str, rpt: str | None, version: str | None) -> Path:
    run_dir = parent / name
    run_dir.mkdir()
    if rpt is not None:
        (run_dir / "model.rpt").write_text(rpt, encoding="utf-8")
    audit = run_dir / "09_audit"
    audit.mkdir()
    payload: dict[str, object] = {"run_id": name}
    if version is not None:
        payload["swmm_version"] = version
    (audit / "experiment_provenance.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return run_dir


class CheckSwmmVersionsTests(unittest.TestCase):
    def test_same_exact_version_ok(self) -> None:
        v = check_swmm_versions_for_compare("5.2.4", "5.2.4")
        self.assertTrue(v.ok)
        self.assertFalse(v.allow_with_override)
        self.assertIn("identical", v.reason)

    def test_same_minor_different_patch_ok_with_advisory(self) -> None:
        v = check_swmm_versions_for_compare("5.2.3", "5.2.4")
        self.assertTrue(v.ok)
        self.assertIn("same SWMM minor", v.reason)

    def test_different_minor_not_ok_allow_with_override(self) -> None:
        v = check_swmm_versions_for_compare("5.1.013", "5.2.4")
        self.assertFalse(v.ok)
        self.assertTrue(v.allow_with_override)
        self.assertIn("different SWMM minor", v.reason)

    def test_unparseable_version_not_ok_allow_with_override(self) -> None:
        v = check_swmm_versions_for_compare("garbage", "5.2.4")
        self.assertFalse(v.ok)
        self.assertTrue(v.allow_with_override)
        self.assertIn("unparseable", v.reason)

    def test_none_version_treated_as_unparseable(self) -> None:
        v = check_swmm_versions_for_compare(None, "5.2.4")
        self.assertFalse(v.ok)
        self.assertEqual(v.version_a, "unknown")

    def test_both_versions_unparseable(self) -> None:
        v = check_swmm_versions_for_compare("not-a-version", "")
        self.assertFalse(v.ok)
        self.assertTrue(v.allow_with_override)

    def test_verdict_is_frozen(self) -> None:
        v = check_swmm_versions_for_compare("5.2.4", "5.2.4")
        with self.assertRaises(Exception):
            v.ok = False  # type: ignore[misc]


class CompareRunsVersionRefusalTests(unittest.TestCase):
    def test_cross_minor_versions_no_override_is_incomparable(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _HEALTHY_RPT, "5.1.013")
            b = _write_run(base, "b", _HEALTHY_RPT, "5.2.4")
            result = compare_runs(a, b)
        self.assertEqual(result.verdict, "incomparable")
        self.assertEqual(result.metric_diffs, {})
        self.assertTrue(
            any("solver_version_mismatch" in note for note in result.notes)
        )

    def test_cross_minor_versions_with_override_computes_diffs(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _HEALTHY_RPT, "5.1.013")
            b = _write_run(base, "b", _HEALTHY_RPT, "5.2.4")
            result = compare_runs(a, b, override_version=True)
        self.assertNotEqual(result.verdict, "incomparable")
        self.assertIn("runoff_continuity_pct", result.metric_diffs)
        self.assertTrue(any("user override accepted" in n for n in result.notes))

    def test_same_minor_patch_drift_is_advisory_not_refusal(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _HEALTHY_RPT, "5.2.3")
            b = _write_run(base, "b", _HEALTHY_RPT, "5.2.4")
            result = compare_runs(a, b)
        self.assertNotEqual(result.verdict, "incomparable")
        self.assertTrue(
            any("solver_version_advisory" in note for note in result.notes)
        )

    def test_both_versions_missing_falls_back_to_metric_path(self) -> None:
        # No swmm_version anywhere → policy should NOT refuse on
        # version (both unknown). The result depends on the metrics.
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _HEALTHY_RPT, None)
            b = _write_run(base, "b", _HEALTHY_RPT, None)
            result = compare_runs(a, b)
        # Tie (identical rpts), and no solver_version_mismatch note.
        self.assertEqual(result.verdict, "tie")
        self.assertFalse(
            any("solver_version_mismatch" in n for n in result.notes)
        )

    def test_parametric_store_fallback_for_swmm_version(self) -> None:
        # provenance lacks swmm_version, but parametric_memory.jsonl
        # carries it.
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = _write_run(base, "a", _HEALTHY_RPT, None)
            b = _write_run(base, "b", _HEALTHY_RPT, None)
            store = base / "parametric_memory.jsonl"
            store.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": "a",
                        "case_name": "a",
                        "swmm_version": "5.1.013",
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "schema_version": "1.0",
                        "run_id": "b",
                        "case_name": "b",
                        "swmm_version": "5.2.4",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = compare_runs(a, b, parametric_store=store)
        self.assertEqual(result.verdict, "incomparable")
        self.assertTrue(
            any("solver_version_mismatch" in n for n in result.notes)
        )


if __name__ == "__main__":
    unittest.main()
