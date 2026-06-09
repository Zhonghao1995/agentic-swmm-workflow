"""New-case onboarding chat flow (Round 7).

Covers ``is_new_case``, ``should_offer_transfer``,
``maybe_offer_onboarding``, ``format_onboarding_chat_block``, and the
parse/apply helpers. The LLM-driven dispatch refactor removed the
per-mode adapter layer (``workflow_modes/``); onboarding now integrates
directly with the planner's flat tool-pick loop.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.feature_flags import MEMORY_INFORMED_ENV
from agentic_swmm.agent.onboarding import (
    OnboardingContext,
    OnboardingDecision,
    apply_onboarding_acceptance,
    format_onboarding_chat_block,
    is_new_case,
    mark_customize,
    maybe_offer_onboarding,
    parse_onboarding_response,
    should_offer_transfer,
)
from agentic_swmm.memory.calibration_memory import (
    CalibrationRecord,
    record_calibration_run,
)
from agentic_swmm.memory.cross_watershed_transfer import TransferRecommendation
from agentic_swmm.memory.parametric_memory import (
    ParametricRecord,
    record_parametric_run,
)
from agentic_swmm.memory.watershed_similarity import WatershedAttributes


def _write_min_inp(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[OPTIONS]\nFLOW_UNITS\tCMS\n"
        "[SUBCATCHMENTS]\n;;Name\tRain\tOutlet\tArea\t%Imperv\tWidth\tSlope\tCurbLen\n"
        "S1\tRG\tJ1\t1.0\t10\t100\t0.01\t0\n"
        "[CONDUITS]\n;;Name\tFrom\tTo\tLen\tN\tInletOff\tOutletOff\tInitFlow\tMaxFlow\n"
        "C1\tJ1\tJ2\t100\t0.013\t0\t0\t0\t0\n",
        encoding="utf-8",
    )


def _seed_calibration_history(tmp: Path) -> tuple[Path, Path]:
    """Drop a calibration row + a candidate INP so the recommender has work."""
    memory_dir = tmp / "memory" / "modeling-memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    calibration_store = memory_dir / "calibration_memory.jsonl"

    record_calibration_run(
        calibration_store,
        CalibrationRecord(
            run_id="20260101-000000_source",
            case_name="source_case",
            use_case="urban_runoff",
            algorithm="sce_ua",
            parameters={"manning_n_overland": 0.22},
            objective_name="nse",
            objective_value=0.82,
        ),
    )

    source_inp = tmp / "cases" / "source_case" / "source_case.inp"
    _write_min_inp(source_inp)
    return calibration_store, source_inp


class IsNewCaseTests(unittest.TestCase):
    def test_zero_rows_for_case_returns_true(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            self.assertTrue(is_new_case("vancouver", parametric_store=store))

    def test_existing_row_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            record_parametric_run(
                store,
                ParametricRecord(
                    run_id="r1",
                    case_name="vancouver",
                ),
            )
            self.assertFalse(is_new_case("vancouver", parametric_store=store))

    def test_empty_case_name_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            self.assertFalse(is_new_case("", parametric_store=store))


class ShouldOfferTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        # Clean env to avoid cross-test leakage.
        self._env_was = os.environ.pop(MEMORY_INFORMED_ENV, None)

    def tearDown(self) -> None:
        if self._env_was is not None:
            os.environ[MEMORY_INFORMED_ENV] = self._env_was
        else:
            os.environ.pop(MEMORY_INFORMED_ENV, None)

    def test_intent_token_with_new_case(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            self.assertTrue(
                should_offer_transfer(
                    "vancouver",
                    "please calibrate this watershed",
                    parametric_store=store,
                )
            )

    def test_no_intent_token_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            self.assertFalse(
                should_offer_transfer(
                    "vancouver",
                    "hello there",
                    parametric_store=store,
                )
            )

    def test_opt_out_env_returns_false(self) -> None:
        os.environ[MEMORY_INFORMED_ENV] = "1"
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            self.assertFalse(
                should_offer_transfer(
                    "vancouver",
                    "calibrate this",
                    parametric_store=store,
                )
            )

    def test_existing_case_returns_false(self) -> None:
        with TemporaryDirectory() as tmp:
            store = Path(tmp) / "parametric_memory.jsonl"
            record_parametric_run(
                store,
                ParametricRecord(run_id="r1", case_name="vancouver"),
            )
            self.assertFalse(
                should_offer_transfer(
                    "vancouver",
                    "calibrate this",
                    parametric_store=store,
                )
            )


class MaybeOfferOnboardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_was = os.environ.pop(MEMORY_INFORMED_ENV, None)

    def tearDown(self) -> None:
        if self._env_was is not None:
            os.environ[MEMORY_INFORMED_ENV] = self._env_was
        else:
            os.environ.pop(MEMORY_INFORMED_ENV, None)

    def test_no_history_returns_no_similar_cases(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parametric = tmp_path / "parametric_memory.jsonl"
            calibration = tmp_path / "calibration_memory.jsonl"
            target_inp = tmp_path / "vancouver.inp"
            _write_min_inp(target_inp)
            decision = maybe_offer_onboarding(
                case_name="vancouver",
                utterance="calibrate this",
                target_inp=target_inp,
                parametric_store=parametric,
                calibration_store=calibration,
                negative_lessons_store=tmp_path / "negative.jsonl",
                storm_library_path=tmp_path / "storm.yaml",
                benchmarks_path=tmp_path / "bench.yaml",
                top_k=3,
            )
            self.assertFalse(decision.triggered)
            self.assertEqual("no_similar_cases", decision.reason)

    def test_with_similar_cases_chat_block_has_all(self) -> None:
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            parametric = tmp_path / "parametric_memory.jsonl"
            calibration_store, _ = _seed_calibration_history(tmp_path)

            target_inp = tmp_path / "vancouver.inp"
            _write_min_inp(target_inp)
            decision = maybe_offer_onboarding(
                case_name="vancouver",
                utterance="calibrate this",
                target_inp=target_inp,
                parametric_store=parametric,
                calibration_store=calibration_store,
                negative_lessons_store=tmp_path / "negative.jsonl",
                storm_library_path=tmp_path / "storm.yaml",
                benchmarks_path=tmp_path / "bench.yaml",
                top_k=3,
            )
            self.assertTrue(decision.triggered, decision.reason)
            self.assertEqual("new_case", decision.reason)
            self.assertGreaterEqual(len(decision.recommendations), 1)
            block = decision.chat_block or ""
            self.assertIn("source_case", block)
            self.assertIn("Recommended starter calibration", block)
            self.assertIn("[Y / n / customize]", block)

    def test_memory_disabled_short_circuits(self) -> None:
        os.environ[MEMORY_INFORMED_ENV] = "1"
        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            decision = maybe_offer_onboarding(
                case_name="vancouver",
                utterance="calibrate this",
                target_inp=None,
                parametric_store=tmp_path / "p.jsonl",
                calibration_store=tmp_path / "c.jsonl",
                negative_lessons_store=tmp_path / "n.jsonl",
                storm_library_path=tmp_path / "s.yaml",
                benchmarks_path=tmp_path / "b.yaml",
            )
            self.assertFalse(decision.triggered)
            self.assertEqual("memory_disabled", decision.reason)


class FormatOnboardingChatBlockTests(unittest.TestCase):
    def test_single_recommendation_without_optional_fields(self) -> None:
        rec = TransferRecommendation(
            target_case="vancouver",
            source_case="saanich",
            similarity=0.81,
            source_calibration_record=CalibrationRecord(
                run_id="r",
                case_name="saanich",
                objective_name="nse",
                objective_value=0.75,
            ),
            proposed_parameters={"manning_n_overland": 0.2},
        )
        block = format_onboarding_chat_block("vancouver", [rec])
        self.assertIn("Starting new case", block)
        self.assertIn("saanich", block)
        self.assertIn("Recommended starter calibration", block)
        # No storm key / failure_patterns supplied → the optional lines
        # must not render.
        self.assertNotIn("Recommended design storm", block)
        self.assertNotIn("Known pitfall", block)
        self.assertIn("[Y / n / customize]", block)

    def test_full_recommendation_renders_optional_lines(self) -> None:
        rec = TransferRecommendation(
            target_case="vancouver",
            source_case="saanich",
            similarity=0.81,
            source_calibration_record=CalibrationRecord(
                run_id="r",
                case_name="saanich",
                objective_name="nse",
                objective_value=0.75,
            ),
            proposed_parameters={"manning_n_overland": 0.2},
            recommended_design_storm={
                "key": "saanich-100yr-1h",
                "depth_mm": 50,
            },
            known_failure_patterns=[
                {
                    "lesson_type": "continuity_fail",
                    "parameters_tried": {"manning_n_overland": 0.5},
                    "note": "ran high",
                    "recorded_at": "2026-01-01T00:00:00Z",
                }
            ],
        )
        block = format_onboarding_chat_block("vancouver", [rec])
        self.assertIn("Recommended design storm", block)
        self.assertIn("saanich-100yr-1h", block)
        self.assertIn("Known pitfall", block)


class ParseOnboardingResponseTests(unittest.TestCase):
    def test_accept_variants(self) -> None:
        self.assertEqual("accept", parse_onboarding_response(""))
        self.assertEqual("accept", parse_onboarding_response("Y"))
        self.assertEqual("accept", parse_onboarding_response("yes"))
        self.assertEqual("accept", parse_onboarding_response("y"))

    def test_decline_variants(self) -> None:
        self.assertEqual("decline", parse_onboarding_response("n"))
        self.assertEqual("decline", parse_onboarding_response("No"))

    def test_customize(self) -> None:
        self.assertEqual("customize", parse_onboarding_response("customize"))
        self.assertEqual("customize", parse_onboarding_response("c"))

    def test_unknown(self) -> None:
        self.assertEqual("unknown", parse_onboarding_response("maybe later"))

    def test_whitespace_is_accept(self) -> None:
        self.assertEqual("accept", parse_onboarding_response("   "))

    def test_case_insensitive_yes(self) -> None:
        self.assertEqual("accept", parse_onboarding_response("YES"))

    def test_case_insensitive_no(self) -> None:
        self.assertEqual("decline", parse_onboarding_response("NO"))


class WorkflowIntentTokenTests(unittest.TestCase):
    """The intent-token gate is permissive across natural phrasings."""

    def _new(self, store: Path, utterance: str) -> bool:
        return should_offer_transfer(
            "vancouver", utterance, parametric_store=store
        )

    def test_calibrate(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertTrue(
                self._new(Path(tmp) / "p.jsonl", "please calibrate this")
            )

    def test_run(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertTrue(self._new(Path(tmp) / "p.jsonl", "run the model"))

    def test_audit(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertTrue(self._new(Path(tmp) / "p.jsonl", "audit this run"))

    def test_simulate(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertTrue(
                self._new(Path(tmp) / "p.jsonl", "simulate the storm")
            )

    def test_setup(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertTrue(
                self._new(Path(tmp) / "p.jsonl", "setup the project")
            )

    def test_bare_question_no_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertFalse(
                self._new(Path(tmp) / "p.jsonl", "what is happening")
            )


class ApplyAcceptanceTests(unittest.TestCase):
    def _decision(self) -> OnboardingDecision:
        rec = TransferRecommendation(
            target_case="vancouver",
            source_case="saanich",
            similarity=0.81,
            source_calibration_record=CalibrationRecord(
                run_id="r",
                case_name="saanich",
                objective_name="nse",
                objective_value=0.75,
            ),
            proposed_parameters={"manning_n_overland": 0.22},
        )
        return OnboardingDecision(
            target_case="vancouver",
            triggered=True,
            reason="new_case",
            recommendations=[rec],
            chat_block="...",
        )

    def test_accept_sets_defaults(self) -> None:
        ctx = mock.Mock()
        ctx.onboarding = None
        decision = self._decision()
        onboarding = apply_onboarding_acceptance(ctx, decision)
        self.assertEqual("accepted", onboarding.mode)
        self.assertEqual({"manning_n_overland": 0.22}, onboarding.defaults)
        self.assertEqual("saanich", onboarding.accepted_source_case)
        self.assertIs(onboarding, ctx.onboarding)

    def test_customize_marks_mode(self) -> None:
        ctx = mock.Mock()
        ctx.onboarding = None
        onboarding = mark_customize(ctx)
        self.assertEqual("customizing", onboarding.mode)
        self.assertIs(onboarding, ctx.onboarding)


if __name__ == "__main__":
    unittest.main()
