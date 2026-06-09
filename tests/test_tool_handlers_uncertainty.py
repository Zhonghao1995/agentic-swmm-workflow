"""Tests for the swmm-uncertainty typed ToolSpec handlers (PR 2, issue #246).

Covers:
- All 5 tool names in the registry.
- is_read_only=False for all 5.
- Required-arg validation returns a _failure dict.
- Args-mappers emit correct camelCase keys per tool.
- Schema property count assertions.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Import AgentToolRegistry FIRST to avoid the circular-import at module load
# (same pattern as test_tool_handlers_calibration.py).
from agentic_swmm.agent.tool_registry import AgentToolRegistry  # noqa: E402

from agentic_swmm.agent.tool_handlers.swmm_uncertainty import (  # noqa: E402
    _sensitivity_oat_args,
    _sensitivity_morris_args,
    _sensitivity_sobol_args,
    _rainfall_ensemble_args,
    _source_decomposition_args,
)
from agentic_swmm.agent.types import ToolCall


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


def _call(name: str, args: dict) -> ToolCall:
    c = MagicMock(spec=ToolCall)
    c.name = name
    c.args = args
    return c


_SESSION = Path("/tmp/test_session")

# Args common to OAT/Morris/Sobol.
_SENSITIVITY_BASE = {
    "base_inp": "/some/model.inp",
    "patch_map": "/some/patch_map.json",
    "observed": "/some/observed.csv",
    "run_root": "/some/run_root",
    "summary_json": "/some/summary.json",
}


# ---------------------------------------------------------------------------
# Registry presence
# ---------------------------------------------------------------------------

UNCERTAINTY_TOOL_NAMES = {
    "swmm_sensitivity_oat",
    "swmm_sensitivity_morris",
    "swmm_sensitivity_sobol",
    "swmm_rainfall_ensemble",
    "swmm_uncertainty_source_decomposition",
}


def test_all_five_uncertainty_tools_in_registry(registry: AgentToolRegistry) -> None:
    names = set(registry.names)
    missing = UNCERTAINTY_TOOL_NAMES - names
    assert not missing, f"uncertainty tools missing from registry: {sorted(missing)}"


@pytest.mark.parametrize("tool_name", sorted(UNCERTAINTY_TOOL_NAMES))
def test_uncertainty_tool_is_not_read_only(tool_name: str, registry: AgentToolRegistry) -> None:
    assert registry.is_read_only(tool_name) is False, (
        f"{tool_name} must have is_read_only=False (writes artefacts)"
    )


# ---------------------------------------------------------------------------
# Schema property assertions
# ---------------------------------------------------------------------------

def _props(registry: AgentToolRegistry, name: str) -> set[str]:
    spec = registry._tools.get(name)  # type: ignore[attr-defined]
    assert spec is not None, f"tool {name!r} not found in registry"
    schema = spec.schema()
    params = schema.get("parameters", {})
    return set(params.get("properties", {}).keys())


def test_oat_schema_has_base_params_and_scan_spec(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_sensitivity_oat")
    assert "base_params" in props
    assert "scan_spec" in props
    assert "base_inp" in props
    assert "summary_json" in props


def test_morris_schema_has_parameter_space_and_tuning(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_sensitivity_morris")
    assert "parameter_space" in props
    assert "morris_r" in props
    assert "morris_levels" in props


def test_sobol_schema_has_parameter_space_and_sobol_n(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_sensitivity_sobol")
    assert "parameter_space" in props
    assert "sobol_n" in props


def test_rainfall_ensemble_schema_has_method_config_run_root(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_rainfall_ensemble")
    assert "method" in props
    assert "config" in props
    assert "run_root" in props
    assert "base_inp" in props
    assert "dry_run" in props


def test_source_decomposition_schema_has_run_dir(registry: AgentToolRegistry) -> None:
    props = _props(registry, "swmm_uncertainty_source_decomposition")
    assert "run_dir" in props


# ---------------------------------------------------------------------------
# Required-arg validation — failure path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_key", ["base_inp", "patch_map", "observed", "run_root", "summary_json"])
def test_oat_fails_on_missing_common_required(missing_key: str) -> None:
    args = {**_SENSITIVITY_BASE, "base_params": "/bp.json", "scan_spec": "/ss.json"}
    del args[missing_key]
    result = _sensitivity_oat_args(_call("swmm_sensitivity_oat", args), _SESSION)
    assert result.get("ok") is False
    assert "summary" in result


def test_oat_fails_on_missing_base_params() -> None:
    args = {**_SENSITIVITY_BASE, "scan_spec": "/ss.json"}
    result = _sensitivity_oat_args(_call("swmm_sensitivity_oat", args), _SESSION)
    assert result.get("ok") is False


def test_oat_fails_on_missing_scan_spec() -> None:
    args = {**_SENSITIVITY_BASE, "base_params": "/bp.json"}
    result = _sensitivity_oat_args(_call("swmm_sensitivity_oat", args), _SESSION)
    assert result.get("ok") is False


def test_morris_fails_on_missing_parameter_space() -> None:
    result = _sensitivity_morris_args(_call("swmm_sensitivity_morris", {**_SENSITIVITY_BASE}), _SESSION)
    assert result.get("ok") is False


def test_sobol_fails_on_missing_parameter_space() -> None:
    result = _sensitivity_sobol_args(_call("swmm_sensitivity_sobol", {**_SENSITIVITY_BASE}), _SESSION)
    assert result.get("ok") is False


def test_rainfall_ensemble_fails_on_missing_method() -> None:
    result = _rainfall_ensemble_args(
        _call("swmm_rainfall_ensemble", {"config": "/cfg.json", "run_root": "/out"}), _SESSION
    )
    assert result.get("ok") is False


def test_rainfall_ensemble_fails_on_invalid_method() -> None:
    result = _rainfall_ensemble_args(
        _call("swmm_rainfall_ensemble", {"method": "bogus", "config": "/cfg.json", "run_root": "/out"}), _SESSION
    )
    assert result.get("ok") is False


def test_rainfall_ensemble_fails_on_missing_config() -> None:
    result = _rainfall_ensemble_args(
        _call("swmm_rainfall_ensemble", {"method": "perturbation", "run_root": "/out"}), _SESSION
    )
    assert result.get("ok") is False


def test_rainfall_ensemble_fails_on_missing_run_root() -> None:
    result = _rainfall_ensemble_args(
        _call("swmm_rainfall_ensemble", {"method": "idf", "config": "/cfg.json"}), _SESSION
    )
    assert result.get("ok") is False


def test_source_decomposition_fails_on_missing_run_dir() -> None:
    result = _source_decomposition_args(_call("swmm_uncertainty_source_decomposition", {}), _SESSION)
    assert result.get("ok") is False


# ---------------------------------------------------------------------------
# Args-mapper camelCase translation — happy path
# ---------------------------------------------------------------------------

def test_oat_args_emits_camel_case_keys() -> None:
    args = {**_SENSITIVITY_BASE, "base_params": "/bp.json", "scan_spec": "/ss.json"}
    result = _sensitivity_oat_args(_call("swmm_sensitivity_oat", args), _SESSION)
    assert result.get("ok") is not False, f"unexpected failure: {result}"
    assert result["baseInp"] == "/some/model.inp"
    assert result["patchMap"] == "/some/patch_map.json"
    assert result["observed"] == "/some/observed.csv"
    assert result["runRoot"] == "/some/run_root"
    assert result["summaryJson"] == "/some/summary.json"
    assert result["baseParams"] == "/bp.json"
    assert result["scanSpec"] == "/ss.json"
    # Snake-case must NOT appear
    assert "base_inp" not in result
    assert "base_params" not in result
    assert "scan_spec" not in result


def test_oat_args_maps_optional_common_fields() -> None:
    args = {
        **_SENSITIVITY_BASE,
        "base_params": "/bp.json",
        "scan_spec": "/ss.json",
        "swmm_node": "J5",
        "swmm_attr": "Total_inflow",
        "aggregate": "daily_mean",
        "obs_start": "2024-01-01",
        "obs_end": "2024-12-31",
        "timestamp_col": "ts",
        "flow_col": "q",
        "time_format": "%Y-%m-%d",
        "seed": 77,
    }
    result = _sensitivity_oat_args(_call("swmm_sensitivity_oat", args), _SESSION)
    assert result.get("ok") is not False
    assert result["swmmNode"] == "J5"
    assert result["swmmAttr"] == "Total_inflow"
    assert result["aggregate"] == "daily_mean"
    assert result["obsStart"] == "2024-01-01"
    assert result["obsEnd"] == "2024-12-31"
    assert result["timestampCol"] == "ts"
    assert result["flowCol"] == "q"
    assert result["timeFormat"] == "%Y-%m-%d"
    assert result["seed"] == 77


def test_morris_args_emits_camel_case_keys() -> None:
    args = {
        **_SENSITIVITY_BASE,
        "parameter_space": "/ps.json",
        "morris_r": 15,
        "morris_levels": 6,
    }
    result = _sensitivity_morris_args(_call("swmm_sensitivity_morris", args), _SESSION)
    assert result.get("ok") is not False
    assert result["parameterSpace"] == "/ps.json"
    assert result["morrisR"] == 15
    assert result["morrisLevels"] == 6
    assert "parameter_space" not in result


def test_sobol_args_emits_camel_case_keys() -> None:
    args = {**_SENSITIVITY_BASE, "parameter_space": "/ps.json", "sobol_n": 512}
    result = _sensitivity_sobol_args(_call("swmm_sensitivity_sobol", args), _SESSION)
    assert result.get("ok") is not False
    assert result["parameterSpace"] == "/ps.json"
    assert result["sobolN"] == 512


def test_rainfall_ensemble_args_perturbation() -> None:
    args = {
        "method": "perturbation",
        "config": "/cfg.json",
        "run_root": "/out",
        "base_inp": "/model.inp",
        "series_name": "TS_GAUGE",
        "swmm_node": "O2",
        "seed": 99,
        "dry_run": True,
    }
    result = _rainfall_ensemble_args(_call("swmm_rainfall_ensemble", args), _SESSION)
    assert result.get("ok") is not False
    assert result["method"] == "perturbation"
    assert result["config"] == "/cfg.json"
    assert result["runRoot"] == "/out"
    assert result["baseInp"] == "/model.inp"
    assert result["seriesName"] == "TS_GAUGE"
    assert result["swmmNode"] == "O2"
    assert result["seed"] == 99
    assert result["dryRun"] is True
    assert "run_root" not in result
    assert "base_inp" not in result


def test_rainfall_ensemble_args_idf_without_base_inp() -> None:
    args = {"method": "idf", "config": "/idf.json", "run_root": "/out"}
    result = _rainfall_ensemble_args(_call("swmm_rainfall_ensemble", args), _SESSION)
    assert result.get("ok") is not False
    assert "baseInp" not in result


def test_source_decomposition_args_maps_run_dir() -> None:
    result = _source_decomposition_args(
        _call("swmm_uncertainty_source_decomposition", {"run_dir": "/runs/2024/my_run"}),
        _SESSION,
    )
    assert result.get("ok") is not False
    assert result["runDir"] == "/runs/2024/my_run"
    assert "run_dir" not in result
