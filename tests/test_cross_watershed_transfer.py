"""Tests for ``agentic_swmm.memory.cross_watershed_transfer`` (PRD-07 Phase 5).

The recommender composes :mod:`watershed_similarity` and
:mod:`calibration_memory` to produce one
:class:`TransferRecommendation` per top-k similar source case. These
tests pin the recommender's pure-function slices:

1. Zero candidates → empty list.
2. Three candidates, ``top_k=1`` → top-1 only, highest similarity.
3. ``top_k=2`` → both honoured, ordering desc by similarity.
4. Candidate INP cannot be located → skipped, others survive.
5. Multiple calibration rows per case → highest ``objective_value`` wins.
6. ``run_dir`` provided → exactly one memory_trace line lands.
7. ``run_dir=None`` → no trace line.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.memory.calibration_memory import (
    CalibrationRecord,
    record_calibration_run,
)
from agentic_swmm.memory.cross_watershed_transfer import (
    DECISION_POINT,
    TransferRecommendation,
    recommend_parameters_for_new_case,
)
from agentic_swmm.memory.watershed_similarity import WatershedAttributes


# Tiny INP that the production extractor still gives a real
# WatershedAttributes for. Used as the "target" so the score path
# is non-trivial.
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


def _attrs(area_ha: float, imperv: float, slope: float = 2.0) -> WatershedAttributes:
    """Pre-built attribute bag used as test fixture for candidates."""
    return WatershedAttributes(
        area_ha=area_ha,
        imperv_pct=imperv,
        mean_slope_pct=slope,
        n_subcatchments=20,
        n_conduits=18,
        n_outfalls=1,
    )


def _seed_calibration(
    store: Path,
    case_name: str,
    *,
    run_id: str = "r1",
    objective_value: float = 0.7,
    parameters: dict[str, float] | None = None,
    objective_name: str = "NSE",
) -> None:
    """Write one CalibrationRecord row into ``store``."""
    record = CalibrationRecord(
        run_id=run_id,
        case_name=case_name,
        use_case="stormwater_event",
        algorithm="sceua",
        parameters=parameters or {"manning_n": 0.013},
        objective_name=objective_name,
        objective_value=objective_value,
        secondary_metrics={"pbias_pct": -3.0},
        swmm5_version="5.2.4",
        n_evaluations=200,
        wall_time_s=120.0,
        created_at="2026-05-19T00:00:00Z",
    )
    record_calibration_run(store, record)


def _write_tiny_target(tmp: Path) -> Path:
    """Write a minimal INP file the production extractor accepts."""
    path = tmp / "new_case.inp"
    path.write_text(_TINY_INP, encoding="utf-8")
    return path


class EmptyCandidatesTests(unittest.TestCase):
    """Slice 1 — no calibration history anywhere → empty list."""

    def test_no_calibration_records_returns_empty(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            # Store doesn't exist on disk; recall_calibration tolerates
            # that and returns []. The recommender must agree.
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={},
                top_k=3,
            )
            self.assertEqual(recs, [])

    def test_calibration_records_with_no_locatable_inp_returns_empty(self) -> None:
        """Rows exist but every candidate's INP is missing."""
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            _seed_calibration(store, "ghost-case-1")
            _seed_calibration(store, "ghost-case-2")
            # No candidate_attributes and no conventional INPs under
            # repo_root → every candidate is skipped.
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                top_k=3,
                repo_root=tmp,  # empty layout
            )
            self.assertEqual(recs, [])


class TopKOrderingTests(unittest.TestCase):
    """Slices 2+3 — ranking honours similarity desc, top_k truncates."""

    def _build_three_candidates(self, store: Path) -> dict[str, WatershedAttributes]:
        # 'twin' is almost identical to the target, 'near' is similar,
        # 'far' is far. All three have a calibration row so the
        # recommender can produce three recommendations if asked.
        _seed_calibration(store, "twin", run_id="r-twin", objective_value=0.80)
        _seed_calibration(store, "near", run_id="r-near", objective_value=0.78)
        _seed_calibration(store, "far", run_id="r-far", objective_value=0.65)
        # Match the target's actual attributes for 'twin' so it scores
        # ~1.0. The target INP yields area_ha=15 and imperv ~43.3%.
        return {
            "twin": _attrs(area_ha=15.0, imperv=43.333),
            "near": _attrs(area_ha=20.0, imperv=50.0),
            "far": _attrs(area_ha=2000.0, imperv=90.0),
        }

    def test_top_k_one_returns_highest_similarity(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            cand_attrs = self._build_three_candidates(store)

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=cand_attrs,
                top_k=1,
            )

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].source_case, "twin")
        self.assertEqual(recs[0].confidence, "memory_informed")
        # The other two candidates are alternatives.
        self.assertEqual(recs[0].n_alternatives, 2)
        # Proposed parameters are a shallow copy of the source record
        # parameters so the caller can mutate freely.
        self.assertEqual(recs[0].proposed_parameters, {"manning_n": 0.013})

    def test_top_k_two_returns_descending_by_similarity(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            cand_attrs = self._build_three_candidates(store)

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=cand_attrs,
                top_k=2,
            )

        self.assertEqual([r.source_case for r in recs], ["twin", "near"])
        # Similarity must be non-increasing.
        self.assertGreaterEqual(recs[0].similarity, recs[1].similarity)

    def test_top_k_three_returns_all_when_three_candidates(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            cand_attrs = self._build_three_candidates(store)

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes=cand_attrs,
                top_k=3,
            )

        # All three returned, sorted desc.
        self.assertEqual([r.source_case for r in recs], ["twin", "near", "far"])
        scores = [r.similarity for r in recs]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_top_k_zero_returns_empty(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            _seed_calibration(store, "twin")
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"twin": _attrs(15.0, 43.3)},
                top_k=0,
            )
            self.assertEqual(recs, [])


class SkipMissingInpTests(unittest.TestCase):
    """Slice 4 — candidate whose INP cannot be located is skipped."""

    def test_partial_candidate_lookup_drops_missing(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            _seed_calibration(store, "twin", run_id="r-twin")
            _seed_calibration(store, "ghost", run_id="r-ghost")

            # Only 'twin' is supplied; 'ghost' has no conventional
            # location either, so it should drop out.
            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"twin": _attrs(15.0, 43.3)},
                top_k=3,
                repo_root=tmp,
            )

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].source_case, "twin")

    def test_conventional_location_lookup_picks_up_present_inp(self) -> None:
        """When candidate_attributes is None, conventional paths fire."""
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            _seed_calibration(store, "twin", run_id="r-twin")
            # Put a 'twin.inp' under cases/twin/twin.inp so the lookup
            # finds it. Mirror the target body so similarity ~ 1.
            cases_dir = tmp / "cases" / "twin"
            cases_dir.mkdir(parents=True, exist_ok=True)
            (cases_dir / "twin.inp").write_text(_TINY_INP, encoding="utf-8")

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                top_k=1,
                repo_root=tmp,
            )

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].source_case, "twin")
        # Similarity should be very high (~1) since both INPs are
        # identical.
        self.assertGreater(recs[0].similarity, 0.9)


class BestRecordPerCaseTests(unittest.TestCase):
    """Slice 5 — multi-row case returns the highest-objective row."""

    def test_picks_record_with_highest_objective_value(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            # Two rows for 'twin' — only the second one's parameters
            # should surface (it has the higher NSE).
            _seed_calibration(
                store,
                "twin",
                run_id="r-old",
                objective_value=0.60,
                parameters={"manning_n": 0.020},
            )
            _seed_calibration(
                store,
                "twin",
                run_id="r-new",
                objective_value=0.85,
                parameters={"manning_n": 0.012},
            )

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"twin": _attrs(15.0, 43.3)},
                top_k=1,
            )

        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].proposed_parameters, {"manning_n": 0.012})
        self.assertEqual(recs[0].source_calibration_record.run_id, "r-new")
        self.assertAlmostEqual(
            recs[0].source_calibration_record.objective_value or 0.0, 0.85
        )

    def test_record_with_no_objective_value_handled(self) -> None:
        """Defensive: a row missing objective_value still sorts to end."""
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            _seed_calibration(
                store,
                "twin",
                run_id="r-good",
                objective_value=0.70,
                parameters={"manning_n": 0.013},
            )
            # Write a second row whose objective_value would not parse.
            # We can do this by writing JSON directly to the store.
            with store.open("a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "schema_version": "1.0",
                            "run_id": "r-bad",
                            "case_name": "twin",
                            "parameters": {"manning_n": 0.50},
                            "objective_name": "NSE",
                            "objective_value": None,
                            "secondary_metrics": {},
                            "use_case": None,
                            "algorithm": None,
                            "swmm5_version": None,
                            "n_evaluations": None,
                            "wall_time_s": None,
                            "created_at": "2026-05-19T00:00:00Z",
                        }
                    )
                    + "\n"
                )

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"twin": _attrs(15.0, 43.3)},
                top_k=1,
            )

        self.assertEqual(len(recs), 1)
        # The valid row wins; the null-objective row is deprioritised.
        self.assertEqual(recs[0].proposed_parameters, {"manning_n": 0.013})


class TraceLoggingTests(unittest.TestCase):
    """Slices 6+7 — run_dir controls memory_trace.jsonl emission."""

    def _read_jsonl(self, path: Path) -> list[dict[str, object]]:
        if not path.is_file():
            return []
        out: list[dict[str, object]] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
        return out

    def test_trace_line_written_when_run_dir_provided(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            run_dir = tmp / "run-001"
            _seed_calibration(store, "twin")

            recs = recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"twin": _attrs(15.0, 43.3)},
                top_k=1,
                run_dir=run_dir,
            )
            lines = self._read_jsonl(run_dir / "memory_trace.jsonl")

        self.assertEqual(len(recs), 1)
        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(lines[0]["decision_point"], DECISION_POINT)
        self.assertEqual(lines[0]["confidence"], "memory_informed")
        self.assertEqual(lines[0]["decision_taken"], "twin")

    def test_no_trace_line_when_run_dir_none(self) -> None:
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            _seed_calibration(store, "twin")

            recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                candidate_attributes={"twin": _attrs(15.0, 43.3)},
                top_k=1,
                run_dir=None,
            )

        # Nothing under tmp should be a memory_trace.jsonl.
        traces = list(tmp.glob("**/memory_trace.jsonl"))
        self.assertEqual(traces, [])

    def test_trace_line_for_empty_candidates_marks_llm(self) -> None:
        """Empty calibration store → run_dir trace line carries ``llm``."""
        with TemporaryDirectory() as raw:
            tmp = Path(raw)
            store = tmp / "calibration_memory.jsonl"
            target = _write_tiny_target(tmp)
            run_dir = tmp / "run-001"
            # No seed calls → store does not exist on disk.

            recommend_parameters_for_new_case(
                target,
                calibration_store=store,
                top_k=3,
                run_dir=run_dir,
            )
            lines = self._read_jsonl(run_dir / "memory_trace.jsonl")

        self.assertEqual(len(lines), 1, lines)
        self.assertEqual(lines[0]["confidence"], "llm")
        self.assertEqual(lines[0]["decision_taken"], "(none)")


class TransferRecommendationDataclassTests(unittest.TestCase):
    """Sanity: the recommendation dataclass is frozen and serialises."""

    def _record(self) -> CalibrationRecord:
        return CalibrationRecord(
            run_id="r1",
            case_name="twin",
            algorithm="sceua",
            parameters={"manning_n": 0.013},
            objective_name="NSE",
            objective_value=0.78,
        )

    def test_frozen(self) -> None:
        rec = TransferRecommendation(
            target_case="new",
            source_case="twin",
            similarity=0.9,
            source_calibration_record=self._record(),
        )
        with self.assertRaises(Exception):
            rec.similarity = 0.0  # type: ignore[misc]

    def test_to_dict_carries_expected_keys(self) -> None:
        rec = TransferRecommendation(
            target_case="new",
            source_case="twin",
            similarity=0.95,
            source_calibration_record=self._record(),
            proposed_parameters={"manning_n": 0.013},
            rationale="twin (sim=0.950, NSE=0.780)",
            confidence="memory_informed",
            n_alternatives=2,
        )
        d = rec.to_dict()
        self.assertEqual(
            set(d.keys()),
            {
                "target_case",
                "source_case",
                "similarity",
                "objective_name",
                "objective_value",
                "algorithm",
                "swmm5_version",
                "proposed_parameters",
                "rationale",
                "confidence",
                "n_alternatives",
                # Round 3 additive enrichment fields.
                "recommended_design_storm",
                "recommended_manning_n",
                "known_failure_patterns",
            },
        )
        self.assertEqual(d["objective_name"], "NSE")
        self.assertAlmostEqual(float(d["objective_value"]), 0.78)
        self.assertEqual(d["n_alternatives"], 2)


if __name__ == "__main__":
    unittest.main()
