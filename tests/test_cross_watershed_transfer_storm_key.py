"""Tests for Round 2 storm-key hook in ``cross_watershed_transfer``.

Contract:
- When a calibration row carries ``metadata.case_design_storm_key`` and
  that key resolves to a storm_library.chicago_hyetographs entry, the
  recommendation's ``rationale`` field mentions it.
- When the row carries no storm key, the rationale is unchanged
  (behaviour matches the pre-Round-2 baseline).
- When the row has a storm key that does NOT resolve in
  storm_library, the rationale is also unchanged (no crash, no mention).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.cross_watershed_transfer import (
    recommend_parameters_for_new_case,
)
from agentic_swmm.memory.watershed_similarity import WatershedAttributes


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


_LIBRARY_YAML = """\
schema_version: "1.0"

chicago_hyetographs:
  vancouver_100yr_3hr_5min:
    idf_params:
      a: 65.4
      b: 0.08
      c: 0.81
    peak_position: 0.4
    duration_min: 180
    interval_min: 5
    citation: pending

huff_user_overrides: {}
scs_user_overrides: {}
user_curated: {}
"""


def _attrs(area_ha: float = 15.0, imperv: float = 43.333) -> WatershedAttributes:
    return WatershedAttributes(
        area_ha=area_ha,
        imperv_pct=imperv,
        mean_slope_pct=2.0,
        n_subcatchments=20,
        n_conduits=18,
        n_outfalls=1,
    )


def _seed_row(
    store: Path,
    case_name: str,
    *,
    metadata: dict | None = None,
    objective_value: float = 0.75,
    run_id: str = "r1",
) -> None:
    """Write one calibration row, optionally with a ``metadata`` block.

    The store schema does not version-stamp the ``metadata`` field
    (it is an extra key on the row) so the reader has to be tolerant
    of its absence. This test seeds rows directly via JSONL so we can
    exercise both branches without modifying the writer.
    """
    payload = {
        "schema_version": "1.0",
        "run_id": run_id,
        "case_name": case_name,
        "use_case": "stormwater_event",
        "algorithm": "sceua",
        "parameters": {"manning_n": 0.013},
        "objective_name": "NSE",
        "objective_value": objective_value,
        "secondary_metrics": {"pbias_pct": -3.0},
        "swmm5_version": "5.2.4",
        "n_evaluations": 200,
        "wall_time_s": 120.0,
        "created_at": "2026-05-19T00:00:00Z",
    }
    if metadata is not None:
        payload["metadata"] = metadata
    store.parent.mkdir(parents=True, exist_ok=True)
    with store.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _write_target_and_library(tmp: Path) -> tuple[Path, Path]:
    """Build a layout that matches the repo-root convention used by
    ``_storm_key_resolves`` (memory/modeling-memory/storm_library.yaml)."""
    target = tmp / "new_case.inp"
    target.write_text(_TINY_INP, encoding="utf-8")
    lib_dir = tmp / "memory" / "modeling-memory"
    lib_dir.mkdir(parents=True, exist_ok=True)
    lib_path = lib_dir / "storm_library.yaml"
    lib_path.write_text(_LIBRARY_YAML, encoding="utf-8")
    return target, lib_path


class StormKeyRationaleTests(unittest.TestCase):
    def test_storm_key_in_metadata_surfaces_in_rationale(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            target, _ = _write_target_and_library(tmp)
            store = tmp / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _seed_row(
                store,
                "saanich-b8",
                metadata={"case_design_storm_key": "vancouver_100yr_3hr_5min"},
            )

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"saanich-b8": _attrs()},
                top_k=1,
                repo_root=tmp,
            )

            self.assertEqual(len(recs), 1)
            rationale = recs[0].rationale
            self.assertIn("saanich-b8", rationale)
            # Mentions the storm library path explicitly so the user
            # can re-run with the same storm.
            self.assertIn("storm_library.chicago_hyetographs.vancouver_100yr_3hr_5min", rationale)

    def test_no_storm_key_rationale_unchanged(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            target, _ = _write_target_and_library(tmp)
            store = tmp / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _seed_row(store, "rouge-a1")  # no metadata block

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"rouge-a1": _attrs()},
                top_k=1,
                repo_root=tmp,
            )

            self.assertEqual(len(recs), 1)
            self.assertNotIn("storm_library", recs[0].rationale)

    def test_unresolved_storm_key_skipped_silently(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            target, _ = _write_target_and_library(tmp)
            store = tmp / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _seed_row(
                store,
                "rouge-a2",
                metadata={"case_design_storm_key": "key_that_does_not_exist"},
            )

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"rouge-a2": _attrs()},
                top_k=1,
                repo_root=tmp,
            )

            self.assertEqual(len(recs), 1)
            # Storm-library substring should be absent — the rationale
            # was not enriched because the key does not resolve.
            self.assertNotIn("storm_library", recs[0].rationale)

    def test_storm_key_with_missing_library_skipped_silently(self) -> None:
        """If the storm_library.yaml file doesn't exist at the
        conventional location, the rationale enrichment must still
        be skipped without raising."""
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            target = tmp / "new_case.inp"
            target.write_text(_TINY_INP, encoding="utf-8")
            # NOTE: no memory/modeling-memory dir created.
            store = tmp / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _seed_row(
                store,
                "rouge-a3",
                metadata={"case_design_storm_key": "vancouver_100yr_3hr_5min"},
            )

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"rouge-a3": _attrs()},
                top_k=1,
                repo_root=tmp,
            )

            self.assertEqual(len(recs), 1)
            self.assertNotIn("storm_library", recs[0].rationale)

    def test_non_dict_metadata_skipped_silently(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            target, _ = _write_target_and_library(tmp)
            store = tmp / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            # metadata is a string instead of dict.
            _seed_row(store, "rouge-a4", metadata=None)
            # Append a corrupt extra row by hand to exercise the
            # non-dict branch.
            with store.open("a", encoding="utf-8") as h:
                h.write(json.dumps({
                    "schema_version": "1.0",
                    "run_id": "r2",
                    "case_name": "rouge-a4",
                    "objective_name": "NSE",
                    "objective_value": 0.5,
                    "parameters": {"manning_n": 0.013},
                    "metadata": "not-a-dict",
                }) + "\n")

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"rouge-a4": _attrs()},
                top_k=1,
                repo_root=tmp,
            )

            self.assertEqual(len(recs), 1)
            self.assertNotIn("storm_library", recs[0].rationale)


if __name__ == "__main__":
    unittest.main()
