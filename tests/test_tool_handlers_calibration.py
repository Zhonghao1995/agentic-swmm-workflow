"""Tests for the swmm-calibration typed ToolSpec handlers (PR 1, issue #246).

Covers:
- All 6 tool names in the registry.
- is_read_only=False for all 6.
- Required-arg validation returns a _failure dict.
- Args-mappers emit correct camelCase keys including ``candidateRunDir``.
- Schema property count assertions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Import AgentToolRegistry FIRST so that tool_registry is fully loaded before
# swmm_calibration is imported. The handler-build calls in swmm_calibration.py
# do a lazy `from tool_registry import _make_mcp_routed_handler`; if
# tool_registry isn't in sys.modules yet that triggers a circular import.
from agentic_swmm.agent.tool_registry import AgentToolRegistry  # noqa: E402 (must be first)

from agentic_swmm.agent.tool_handlers.swmm_calibration import (  # noqa: E402
    _calibrate_args,
    _calibrate_dream_zs_args,
    _calibrate_search_args,
    _calibrate_sceua_args,
    _sensitivity_scan_args,
    _validate_args,
)
from agentic_swmm.agent.types import ToolCall


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


def _call(name: str, args: dict) -> ToolCall:
    """Build a minimal ToolCall for mapper tests."""
    c = MagicMock(spec=ToolCall)
    c.name = name
    c.args = args
    return c


_SESSION = Path("/tmp/test_session")

_COMMON_ARGS = {
    "base_inp": "/some/model.inp",
    "patch_map": "/some/patch_map.json",
    "observed": "/some/observed.csv",
    "run_root": "/some/run_root",
    "summary_json": "/some/summary.json",
}


# ---------------------------------------------------------------------------
# Registry presence
# ---------------------------------------------------------------------------

CALIBRATION_TOOL_NAMES = {
    "swmm_sensitivity_scan",
    "swmm_calibrate",
    "swmm_calibrate_search",
    "swmm_calibrate_sceua",
    "swmm_calibrate_dream_zs",
    "swmm_validate",
}


def test_all_six_calibration_tools_in_registry(registry: AgentToolRegistry) -> None:
    names = set(registry.names)
    missing = CALIBRATION_TOOL_NAMES - names
    assert not missing, f"calibration tools missing from registry: {sorted(missing)}"


@pytest.mark.parametrize("tool_name", sorted(CALIBRATION_TOOL_NAMES))
def test_calibration_tool_is_not_read_only(tool_name: str, registry: AgentToolRegistry) -> None:
    assert registry.is_read_only(tool_name) is False, (
        f"{tool_name} must have is_read_only=False (it runs SWMM and writes files)"
    )


# ---------------------------------------------------------------------------
# Schema property counts
# (Common base = 17 props; tool-specific extras vary)
# ---------------------------------------------------------------------------

def _props(registry: AgentToolRegistry, name: str) -> set[str]:
    # Access via the private _tools dict so we get the ToolSpec's schema().
    spec = registry._tools.get(name)  # type: ignore[attr-defined]
    assert spec is not None, f"tool {name!r} not found in registry"
    schema = spec.schema()
    params = schema.get("parameters", {})
    return set(params.get("properties", {}).keys())


def test_sensitivity_scan_schema_has_parameter_sets(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_sensitivity_scan")
    assert "parameter_sets" in props
    assert "base_inp" in props
    assert "summary_json" in props


def test_calibrate_schema_has_parameter_sets_and_optional_promotion(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_calibrate")
    assert "parameter_sets" in props
    assert "best_params_out" in props
    assert "candidate_run_dir" in props


def test_calibrate_search_schema_has_search_space_and_strategy(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_calibrate_search")
    assert "search_space" in props
    assert "strategy" in props
    assert "iterations" in props
    assert "candidate_run_dir" in props


def test_calibrate_sceua_schema_has_sceua_ngs_and_convergence_csv(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_calibrate_sceua")
    assert "sceua_ngs" in props
    assert "convergence_csv" in props
    assert "candidate_run_dir" in props


def test_calibrate_dream_zs_schema_has_dream_params(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_calibrate_dream_zs")
    assert "dream_chains" in props
    assert "dream_sigma" in props
    assert "dream_rhat_threshold" in props
    assert "dream_runs_after_convergence" in props
    assert "candidate_run_dir" in props


def test_validate_schema_has_best_params(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_validate")
    assert "best_params" in props
    assert "trial_name" in props


# ---------------------------------------------------------------------------
# Required-arg validation — failure path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_key", ["base_inp", "patch_map", "observed", "run_root", "summary_json"])
def test_sensitivity_scan_fails_on_missing_common_required(missing_key: str) -> None:
    args = {**_COMMON_ARGS, "parameter_sets": "/sets.json"}
    del args[missing_key]
    result = _sensitivity_scan_args(_call("swmm_sensitivity_scan", args), _SESSION)
    assert result.get("ok") is False
    assert "summary" in result


def test_sensitivity_scan_fails_on_missing_parameter_sets() -> None:
    result = _sensitivity_scan_args(_call("swmm_sensitivity_scan", {**_COMMON_ARGS}), _SESSION)
    assert result.get("ok") is False


def test_calibrate_fails_on_missing_parameter_sets() -> None:
    result = _calibrate_args(_call("swmm_calibrate", {**_COMMON_ARGS}), _SESSION)
    assert result.get("ok") is False


def test_calibrate_search_fails_on_missing_search_space() -> None:
    result = _calibrate_search_args(_call("swmm_calibrate_search", {**_COMMON_ARGS}), _SESSION)
    assert result.get("ok") is False


def test_calibrate_sceua_fails_on_missing_search_space() -> None:
    result = _calibrate_sceua_args(_call("swmm_calibrate_sceua", {**_COMMON_ARGS}), _SESSION)
    assert result.get("ok") is False


def test_calibrate_dream_zs_fails_on_missing_search_space() -> None:
    result = _calibrate_dream_zs_args(_call("swmm_calibrate_dream_zs", {**_COMMON_ARGS}), _SESSION)
    assert result.get("ok") is False


def test_validate_fails_on_missing_best_params() -> None:
    result = _validate_args(_call("swmm_validate", {**_COMMON_ARGS}), _SESSION)
    assert result.get("ok") is False


# ---------------------------------------------------------------------------
# Args-mapper camelCase translation — happy path
# ---------------------------------------------------------------------------

def test_sensitivity_scan_args_emits_camel_case_required_keys() -> None:
    args = {**_COMMON_ARGS, "parameter_sets": "/sets.json"}
    result = _sensitivity_scan_args(_call("swmm_sensitivity_scan", args), _SESSION)
    assert "baseInp" in result
    assert "patchMap" in result
    assert "observed" in result
    assert "runRoot" in result
    assert "summaryJson" in result
    assert "parameterSets" in result
    # Snake-case keys must NOT appear
    assert "base_inp" not in result
    assert "patch_map" not in result
    assert "summary_json" not in result


def test_calibrate_args_includes_candidate_run_dir() -> None:
    args = {
        **_COMMON_ARGS,
        "parameter_sets": "/sets.json",
        "candidate_run_dir": "/runs/cand_001",
    }
    result = _calibrate_args(_call("swmm_calibrate", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["candidateRunDir"] == "/runs/cand_001"


def test_calibrate_search_args_includes_candidate_run_dir() -> None:
    args = {
        **_COMMON_ARGS,
        "search_space": "/space.json",
        "candidate_run_dir": "/runs/cand_search",
        "strategy": "lhs",
        "iterations": 20,
        "seed": 99,
    }
    result = _calibrate_search_args(_call("swmm_calibrate_search", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["candidateRunDir"] == "/runs/cand_search"
    assert result["strategy"] == "lhs"
    assert result["iterations"] == 20
    assert result["seed"] == 99


def test_calibrate_sceua_args_includes_candidate_run_dir() -> None:
    args = {
        **_COMMON_ARGS,
        "search_space": "/space.json",
        "candidate_run_dir": "/runs/cand_sceua",
        "sceua_ngs": 6,
        "convergence_csv": "/conv.csv",
    }
    result = _calibrate_sceua_args(_call("swmm_calibrate_sceua", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["candidateRunDir"] == "/runs/cand_sceua"
    assert result["sceuaNgs"] == 6
    assert result["convergenceCsv"] == "/conv.csv"


def test_calibrate_dream_zs_args_includes_candidate_run_dir() -> None:
    args = {
        **_COMMON_ARGS,
        "search_space": "/space.json",
        "candidate_run_dir": "/runs/cand_dream",
        "dream_chains": 8,
        "dream_sigma": 0.05,
        "dream_rhat_threshold": 1.1,
    }
    result = _calibrate_dream_zs_args(_call("swmm_calibrate_dream_zs", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["candidateRunDir"] == "/runs/cand_dream"
    assert result["dreamChains"] == 8
    assert result["dreamSigma"] == pytest.approx(0.05)
    assert result["dreamRhatThreshold"] == pytest.approx(1.1)


def test_validate_args_emits_best_params_camel() -> None:
    args = {
        **_COMMON_ARGS,
        "best_params": "/best.json",
        "trial_name": "holdout_2024",
    }
    result = _validate_args(_call("swmm_validate", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["bestParams"] == "/best.json"
    assert result["trialName"] == "holdout_2024"


def test_common_optional_args_map_correctly() -> None:
    """Common optional args (obs_start, obs_end, etc.) translate to camelCase."""
    args = {
        **_COMMON_ARGS,
        "parameter_sets": "/sets.json",
        "obs_start": "2024-01-01",
        "obs_end": "2024-12-31",
        "timestamp_col": "datetime",
        "flow_col": "flow_m3s",
        "swmm_node": "J5",
        "swmm_attr": "Total_inflow",
        "objective": "kge",
        "dry_run": True,
        "ranking_top": 5,
    }
    result = _sensitivity_scan_args(_call("swmm_sensitivity_scan", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["obsStart"] == "2024-01-01"
    assert result["obsEnd"] == "2024-12-31"
    assert result["timestampCol"] == "datetime"
    assert result["flowCol"] == "flow_m3s"
    assert result["swmmNode"] == "J5"
    assert result["swmmAttr"] == "Total_inflow"
    assert result["objective"] == "kge"
    assert result["dryRun"] is True
    assert result["rankingTop"] == 5
