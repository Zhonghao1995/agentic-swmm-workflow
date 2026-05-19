"""Round 3 transfer-enrichment tests.

The recommender now returns:

- ``recommended_design_storm``: the storm-library spec resolved from
  the source case's ``metadata.case_design_storm_key``, or ``None``.
- ``recommended_manning_n``: calibrated parameter values whose key
  starts with a ``manning_n_*`` prefix from
  ``reference_benchmarks.yaml``. Empty dict when nothing matches.
- ``known_failure_patterns``: lessons from ``negative_lessons.jsonl``
  associated with the source case. Empty list when none exist.

These tests exercise each field independently so a regression that
drops one does not mask another.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.cli import build_parser
from agentic_swmm.memory.cross_watershed_transfer import (
    recommend_parameters_for_new_case,
)
from agentic_swmm.memory.watershed_similarity import WatershedAttributes


def _write_inp(path: Path) -> None:
    """Minimal INP body so ``extract_attributes_from_inp`` does not raise."""
    path.write_text(
        "[TITLE]\ntest case\n\n[SUBCATCHMENTS]\n",
        encoding="utf-8",
    )


def _calibration_row(
    *,
    run_id: str,
    case_name: str,
    parameters: dict[str, float] | None = None,
    storm_key: str | None = None,
    objective_value: float = 0.8,
) -> dict[str, object]:
    row: dict[str, object] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "case_name": case_name,
        "use_case": "stormwater_event",
        "algorithm": "sceua",
        "parameters": parameters or {"manning_n_overland_grass": 0.05},
        "objective_name": "NSE",
        "objective_value": objective_value,
        "secondary_metrics": {},
        "swmm5_version": "5.2.4",
    }
    if storm_key is not None:
        row["metadata"] = {"case_design_storm_key": storm_key}
    return row


def _write_calibration_store(
    path: Path, rows: list[dict[str, object]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _write_storm_library(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""\
schema_version: "1.0"
chicago_hyetographs:
  {key}:
    idf_params:
      a: 1000.0
      b: 30.0
      c: 0.8
    duration_min: 60
    peak_position: 0.4
    notes: "fixture"
""",
        encoding="utf-8",
    )


def _write_benchmarks(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """\
schema_version: "1.0"
manning_n_overland:
  grass_short:
    typical: 0.15
manning_n_pipes:
  hdpe:
    typical: 0.012
""",
        encoding="utf-8",
    )


def _write_negative_lessons(path: Path, case_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "neg-1",
                    "case_name": case_name,
                    "lesson_type": "continuity_fail",
                    "parameters_tried": {"manning_n_overland_grass": 0.5},
                    "metric_observed": {"runoff_continuity_pct": 12.4},
                    "note": "ran far too high",
                    "recorded_at": "2026-04-01T00:00:00Z",
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "run_id": "neg-2",
                    "case_name": case_name,
                    "lesson_type": "calibration_diverged",
                    "parameters_tried": {"manning_n_overland_grass": 1.0},
                    "metric_observed": {},
                    "note": "sceua never converged",
                    "recorded_at": "2026-04-02T00:00:00Z",
                }
            )
            + "\n"
        )


def _twin_attrs() -> dict[str, WatershedAttributes]:
    twin = WatershedAttributes(
        area_ha=200.0,
        imperv_pct=40.0,
        mean_slope_pct=2.0,
        n_subcatchments=10,
        n_conduits=11,
        n_outfalls=1,
    )
    return {"twin": twin}


class DesignStormFieldTests(unittest.TestCase):
    def test_storm_field_populated_when_source_has_key(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store,
                [
                    _calibration_row(
                        run_id="r1",
                        case_name="twin",
                        storm_key="vic_10yr",
                    )
                ],
            )
            storm_library = base / "memory" / "modeling-memory" / "storm_library.yaml"
            _write_storm_library(storm_library, "vic_10yr")
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
                storm_library_path=storm_library,
            )
        self.assertEqual(len(recs), 1)
        self.assertIsNotNone(recs[0].recommended_design_storm)
        self.assertEqual(recs[0].recommended_design_storm["key"], "vic_10yr")

    def test_storm_field_none_when_source_has_no_key(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store, [_calibration_row(run_id="r1", case_name="twin")]
            )
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
            )
        self.assertIsNone(recs[0].recommended_design_storm)

    def test_storm_field_none_when_key_does_not_resolve(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store,
                [
                    _calibration_row(
                        run_id="r1",
                        case_name="twin",
                        storm_key="not-in-library",
                    )
                ],
            )
            storm_library = base / "memory" / "modeling-memory" / "storm_library.yaml"
            _write_storm_library(storm_library, "vic_10yr")
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
                storm_library_path=storm_library,
            )
        self.assertIsNone(recs[0].recommended_design_storm)


class ManningNFieldTests(unittest.TestCase):
    def test_manning_n_populated_when_parameter_matches_known_prefix(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store,
                [
                    _calibration_row(
                        run_id="r1",
                        case_name="twin",
                        parameters={
                            "manning_n_overland_grass": 0.05,
                            "manning_n_pipes_hdpe": 0.013,
                            "max_infil_rate": 25.0,
                        },
                    )
                ],
            )
            benchmarks = base / "memory" / "modeling-memory" / "reference_benchmarks.yaml"
            _write_benchmarks(benchmarks)
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
                benchmarks_path=benchmarks,
            )
        m = recs[0].recommended_manning_n
        self.assertIn("manning_n_overland_grass", m)
        self.assertIn("manning_n_pipes_hdpe", m)
        # Non-Manning parameter is filtered out.
        self.assertNotIn("max_infil_rate", m)

    def test_manning_n_empty_when_no_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store,
                [
                    _calibration_row(
                        run_id="r1",
                        case_name="twin",
                        parameters={"max_infil_rate": 25.0},
                    )
                ],
            )
            benchmarks = base / "memory" / "modeling-memory" / "reference_benchmarks.yaml"
            _write_benchmarks(benchmarks)
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
                benchmarks_path=benchmarks,
            )
        self.assertEqual(recs[0].recommended_manning_n, {})

    def test_manning_n_empty_when_benchmarks_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store,
                [
                    _calibration_row(
                        run_id="r1",
                        case_name="twin",
                        parameters={"manning_n_overland_grass": 0.05},
                    )
                ],
            )
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
            )
        # No benchmarks file → no prefixes → empty dict.
        self.assertEqual(recs[0].recommended_manning_n, {})


class FailurePatternsFieldTests(unittest.TestCase):
    def test_failure_patterns_populated_for_source_case(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store, [_calibration_row(run_id="r1", case_name="twin")]
            )
            neg_store = base / "memory" / "modeling-memory" / "negative_lessons.jsonl"
            _write_negative_lessons(neg_store, case_name="twin")
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
                negative_lessons_store=neg_store,
            )
        lessons = recs[0].known_failure_patterns
        self.assertEqual(len(lessons), 2)
        # Newest-first ordering: neg-2 has the later recorded_at.
        self.assertEqual(lessons[0]["lesson_type"], "calibration_diverged")

    def test_failure_patterns_empty_when_store_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store, [_calibration_row(run_id="r1", case_name="twin")]
            )
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
            )
        self.assertEqual(recs[0].known_failure_patterns, [])

    def test_failure_patterns_isolated_by_case_name(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
            _write_calibration_store(
                store, [_calibration_row(run_id="r1", case_name="twin")]
            )
            neg_store = base / "memory" / "modeling-memory" / "negative_lessons.jsonl"
            _write_negative_lessons(neg_store, case_name="OTHER")
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=_twin_attrs(),
                top_k=1,
                repo_root=base,
                negative_lessons_store=neg_store,
            )
        self.assertEqual(recs[0].known_failure_patterns, [])


def _dispatch(argv: list[str]) -> tuple[int, str, str]:
    parser = build_parser()
    args = parser.parse_args(argv)
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = int(args.func(args) or 0)
    return rc, out.getvalue(), err.getvalue()


class TransferCliEnrichmentTests(unittest.TestCase):
    def _setup_full_fixture(self, base: Path) -> dict[str, Path]:
        target = base / "new.inp"
        # The CLI uses ``stem`` for the target case label and the
        # production attribute extractor for the target — write a real
        # minimal INP and rely on conventional-location lookup for the
        # source case attributes.
        target.write_text(
            "[SUBCATCHMENTS]\nS1 RG1 J1 1.0 50 100 0.5 0\n",
            encoding="utf-8",
        )
        # Also write the source's INP at the conventional location.
        source_dir = base / "cases" / "twin"
        source_dir.mkdir(parents=True)
        (source_dir / "twin.inp").write_text(
            "[SUBCATCHMENTS]\nS1 RG1 J1 1.0 50 100 0.5 0\n",
            encoding="utf-8",
        )
        store = base / "memory" / "modeling-memory" / "calibration_memory.jsonl"
        _write_calibration_store(
            store,
            [
                _calibration_row(
                    run_id="r1",
                    case_name="twin",
                    storm_key="vic_10yr",
                    parameters={"manning_n_overland_grass": 0.05},
                )
            ],
        )
        storm = base / "memory" / "modeling-memory" / "storm_library.yaml"
        _write_storm_library(storm, "vic_10yr")
        benchmarks = base / "memory" / "modeling-memory" / "reference_benchmarks.yaml"
        _write_benchmarks(benchmarks)
        neg = base / "memory" / "modeling-memory" / "negative_lessons.jsonl"
        _write_negative_lessons(neg, case_name="twin")
        return {
            "target": target,
            "store": store,
            "storm": storm,
            "benchmarks": benchmarks,
            "neg": neg,
            "repo_root": base,
        }

    def test_cli_default_prints_three_enrichment_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = self._setup_full_fixture(base)
            rc, out, _ = _dispatch(
                [
                    "transfer",
                    "--inp",
                    str(cfg["target"]),
                    "--calibration-store",
                    str(cfg["store"]),
                    "--repo-root",
                    str(cfg["repo_root"]),
                    "--storm-library",
                    str(cfg["storm"]),
                    "--negative-lessons",
                    str(cfg["neg"]),
                    "--benchmarks-path",
                    str(cfg["benchmarks"]),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Recommended design storm", out)
        self.assertIn("Recommended Manning's n", out)
        self.assertIn("Known failure patterns", out)

    def test_cli_json_includes_all_new_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = self._setup_full_fixture(base)
            rc, out, _ = _dispatch(
                [
                    "transfer",
                    "--inp",
                    str(cfg["target"]),
                    "--calibration-store",
                    str(cfg["store"]),
                    "--repo-root",
                    str(cfg["repo_root"]),
                    "--storm-library",
                    str(cfg["storm"]),
                    "--negative-lessons",
                    str(cfg["neg"]),
                    "--benchmarks-path",
                    str(cfg["benchmarks"]),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertGreaterEqual(len(payload["recommendations"]), 1)
        r0 = payload["recommendations"][0]
        self.assertIn("recommended_design_storm", r0)
        self.assertIn("recommended_manning_n", r0)
        self.assertIn("known_failure_patterns", r0)

    def test_cli_omits_section_when_field_empty(self) -> None:
        # No storm_library wired → storm section not printed.
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = self._setup_full_fixture(base)
            # Replace the calibration row with one that has no storm
            # key so the design-storm enrichment yields None.
            _write_calibration_store(
                cfg["store"],
                [_calibration_row(run_id="r1", case_name="twin")],
            )
            rc, out, _ = _dispatch(
                [
                    "transfer",
                    "--inp",
                    str(cfg["target"]),
                    "--calibration-store",
                    str(cfg["store"]),
                    "--repo-root",
                    str(cfg["repo_root"]),
                    "--storm-library",
                    str(cfg["storm"]),
                    "--negative-lessons",
                    str(cfg["neg"]),
                    "--benchmarks-path",
                    str(cfg["benchmarks"]),
                ]
            )
        self.assertEqual(rc, 0)
        self.assertNotIn("Recommended design storm", out)


class MemoryInformedPolicyWiringTests(unittest.TestCase):
    def test_build_transfer_lookup_returns_callable(self) -> None:
        from agentic_swmm.agent.memory_informed_policy import (
            build_transfer_lookup,
        )

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "new.inp"
            _write_inp(target)
            store = base / "calibration.jsonl"
            _write_calibration_store(
                store, [_calibration_row(run_id="r1", case_name="twin")]
            )
            lookup = build_transfer_lookup(
                target,
                calibration_store=store,
                top_k=1,
            )
            recs = lookup()
        self.assertIsInstance(recs, list)

    def test_build_transfer_lookup_threads_storm_and_lessons(self) -> None:
        from agentic_swmm.agent.memory_informed_policy import (
            build_transfer_lookup,
        )

        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            cfg = TransferCliEnrichmentTests()._setup_full_fixture(base)
            lookup = build_transfer_lookup(
                cfg["target"],
                calibration_store=cfg["store"],
                top_k=1,
                storm_library_path=cfg["storm"],
                negative_lessons_store=cfg["neg"],
                benchmarks_path=cfg["benchmarks"],
            )
            recs = lookup()
        self.assertGreaterEqual(len(recs), 1)
        self.assertIsNotNone(recs[0].recommended_design_storm)
        self.assertIn(
            "manning_n_overland_grass", recs[0].recommended_manning_n
        )
        self.assertGreaterEqual(len(recs[0].known_failure_patterns), 1)


if __name__ == "__main__":
    unittest.main()
