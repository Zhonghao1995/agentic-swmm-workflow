"""Tests for ``agentic_swmm.agent.error_remediation`` (PRD-08 A.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentic_swmm.agent.error_remediation import (
    RemediationError,
    case_not_found,
    fuzzy_match_suggestions,
    parameter_lookup_error,
    staged_facts_empty,
    storm_library_not_found,
    transfer_empty_result,
)


class TestRemediationErrorFormat:
    def test_all_three_lines_when_cause_and_hint_supplied(self) -> None:
        err = RemediationError(
            summary="something broke",
            cause="missing file",
            hint="run bootstrap",
        )
        out = err.format_for_stderr()
        lines = out.splitlines()
        assert lines[0] == "error: something broke"
        assert lines[1] == "  cause: missing file"
        assert lines[2] == "  hint:  run bootstrap"

    def test_cause_only(self) -> None:
        err = RemediationError(summary="broke", cause="x")
        out = err.format_for_stderr()
        assert "cause: x" in out
        assert "hint:" not in out

    def test_hint_only(self) -> None:
        err = RemediationError(summary="broke", hint="x")
        out = err.format_for_stderr()
        assert "hint:" in out
        assert "cause:" not in out

    def test_summary_only(self) -> None:
        err = RemediationError(summary="broke")
        out = err.format_for_stderr()
        assert out == "error: broke"

    def test_to_dict_round_trip(self) -> None:
        err = RemediationError(summary="s", cause="c", hint="h")
        assert err.to_dict() == {"summary": "s", "cause": "c", "hint": "h"}


class TestFuzzyMatchSuggestions:
    def test_matches_close_typo(self) -> None:
        suggestions = fuzzy_match_suggestions(
            query="maning_n_overland",
            choices=["manning_n_overland", "depression_storage"],
        )
        assert "manning_n_overland" in suggestions

    def test_returns_empty_when_no_close_match(self) -> None:
        suggestions = fuzzy_match_suggestions(
            query="zzz",
            choices=["alpha", "beta"],
        )
        assert suggestions == []

    def test_blank_query_returns_empty(self) -> None:
        assert fuzzy_match_suggestions(query="", choices=["a"]) == []

    def test_respects_max_suggestions(self) -> None:
        suggestions = fuzzy_match_suggestions(
            query="manning_n",
            choices=["manning_n_overland", "manning_n_conduit", "manning_n_lid"],
            max_suggestions=2,
            min_similarity=0.3,
        )
        assert len(suggestions) <= 2


class TestParameterLookupError:
    def test_unknown_parameter_with_similar_names(self) -> None:
        err = parameter_lookup_error(
            parameter_name="maning_n_overland.asphalt",
            benchmarks_path=Path("/x/reference_benchmarks.yaml"),
            similar_names=["manning_n_overland.asphalt"],
            failure_mode="unknown_parameter",
        )
        out = err.format_for_stderr()
        assert "unknown parameter" in out
        assert "did you mean: manning_n_overland.asphalt" in out

    def test_unknown_parameter_without_similar_names(self) -> None:
        err = parameter_lookup_error(
            parameter_name="totally.unknown",
            benchmarks_path=Path("/x/ref.yaml"),
            similar_names=[],
            failure_mode="unknown_parameter",
        )
        out = err.format_for_stderr()
        assert "did you mean" not in out
        assert "bootstrap memory" in out or "open" in out

    def test_leaf_uncurated(self) -> None:
        err = parameter_lookup_error(
            parameter_name="manning_n_overland.asphalt",
            benchmarks_path=Path("/x/ref.yaml"),
            failure_mode="leaf_uncurated",
        )
        out = err.format_for_stderr()
        assert "un-curated" in out
        assert err.hint is not None and "populate" in err.hint

    def test_missing_citation_key(self) -> None:
        err = parameter_lookup_error(
            parameter_name="foo.bar",
            benchmarks_path=Path("/x/ref.yaml"),
            failure_mode="missing_citation_key",
        )
        out = err.format_for_stderr()
        assert "citation" in out.lower()

    def test_citation_unregistered(self) -> None:
        err = parameter_lookup_error(
            parameter_name="foo.bar",
            benchmarks_path=Path("/x/ref.yaml"),
            citations_path=Path("/x/citations.yaml"),
            failure_mode="citation_unregistered",
            citation_key="huber_dickinson_1988",
        )
        out = err.format_for_stderr()
        assert "huber_dickinson_1988" in out
        assert "not registered" in out
        assert "citations.yaml" in out


class TestCaseNotFound:
    def test_with_close_candidates(self) -> None:
        err = case_not_found(slug="tod-creek", candidates=["todcreek", "tecnopolo"])
        out = err.format_for_stderr()
        assert "tod-creek" in out
        assert "did you mean: todcreek?" in out

    def test_no_close_candidates(self) -> None:
        err = case_not_found(slug="totally-fresh", candidates=["xyz", "abc"])
        out = err.format_for_stderr()
        assert "did you mean" not in out
        assert "aiswmm list cases" in out

    def test_empty_candidates(self) -> None:
        err = case_not_found(slug="anything", candidates=[])
        out = err.format_for_stderr()
        assert "anything" in out


class TestTransferEmptyResult:
    def test_store_missing(self) -> None:
        err = transfer_empty_result(
            calibration_store_exists=False,
            similar_cases_found=0,
            store_path=Path("/x/calibration_memory.jsonl"),
        )
        out = err.format_for_stderr()
        assert "bootstrap" in out
        assert "/x/calibration_memory.jsonl" in out

    def test_store_empty(self) -> None:
        err = transfer_empty_result(
            calibration_store_exists=True, similar_cases_found=0
        )
        out = err.format_for_stderr()
        assert "calibrate at least one" in out

    def test_no_similar_cases(self) -> None:
        err = transfer_empty_result(
            calibration_store_exists=True, similar_cases_found=5
        )
        out = err.format_for_stderr()
        assert "similarity threshold" in out


class TestStormLibraryNotFound:
    def test_library_missing(self) -> None:
        err = storm_library_not_found(
            entry_key="rome_100yr",
            library_path=Path("/x/storm_library.yaml"),
            failure_mode="library_missing",
        )
        out = err.format_for_stderr()
        assert "bootstrap memory" in out
        assert "does not exist" in out

    def test_entry_missing(self) -> None:
        err = storm_library_not_found(
            entry_key="not_here",
            library_path=Path("/x/storm_library.yaml"),
            available_keys=["alpha", "beta", "gamma"],
            failure_mode="entry_missing",
        )
        out = err.format_for_stderr()
        assert "alpha" in out
        assert "beta" in out

    def test_entry_placeholder(self) -> None:
        err = storm_library_not_found(
            entry_key="rome_100yr",
            library_path=Path("/x/storm_library.yaml"),
            failure_mode="entry_placeholder",
        )
        out = err.format_for_stderr()
        assert "placeholder" in out
        assert "idf_params" in out


class TestStagedFactsEmpty:
    def test_hints_record_fact(self) -> None:
        err = staged_facts_empty(staging_md=Path("/x/facts_staging.md"))
        out = err.format_for_stderr()
        assert "record_fact" in out
        assert "/x/facts_staging.md" in out
