"""Wiring tests for generate_design_storm (PR #256 follow-up, design-storm wiring).

Verifies that the generate_design_storm ToolSpec is correctly wired as an
MCP-routed tool on the swmm-climate server:

  1. Tool is registered in AgentToolRegistry with is_read_only=False.
  2. Schema has the correct required fields.
  3. Args mapper produces camelCase keys matching the MCP server Zod schema.
  4. Missing required args produce a _failure dict (ok=False).
  5. ExpectedBinding row is present in mcp_coverage.EXPECTED_BINDINGS.
  6. _DETERMINISTIC_BINDINGS maps generate_design_storm → swmm-climate.
  7. SkillRouter.tools_for("swmm-climate") includes generate_design_storm.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.tool_handlers.swmm_climate import _generate_design_storm_args
from agentic_swmm.agent.types import ToolCall


_SESSION = Path("/tmp/test_session_design_storm_pr256")


@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


def _call(name: str, args: dict) -> ToolCall:
    c = MagicMock(spec=ToolCall)
    c.name = name
    c.args = args
    return c


# ---------------------------------------------------------------------------
# 1. Registry presence and is_read_only
# ---------------------------------------------------------------------------

def test_generate_design_storm_in_registry(registry: AgentToolRegistry) -> None:
    assert "generate_design_storm" in registry.names


def test_generate_design_storm_is_not_read_only(registry: AgentToolRegistry) -> None:
    spec = registry._tools["generate_design_storm"]
    assert spec.is_read_only is False


# ---------------------------------------------------------------------------
# 2. Schema required fields
# ---------------------------------------------------------------------------

def test_generate_design_storm_schema_required_fields(registry: AgentToolRegistry) -> None:
    spec = registry._tools["generate_design_storm"]
    required = spec.parameters.get("required", [])
    assert "method" in required
    assert "duration_min" in required
    assert "out_json" in required
    assert "out_timeseries" in required


def test_generate_design_storm_schema_optional_fields(registry: AgentToolRegistry) -> None:
    spec = registry._tools["generate_design_storm"]
    props = spec.parameters.get("properties", {})
    for field in [
        "form", "return_period", "dt", "r",
        "a1", "c_coeff", "b", "n",
        "a_coeff", "c_exp",
        "idf_csv", "idf_json", "series_name",
    ]:
        assert field in props, f"Expected field '{field}' in generate_design_storm schema"


def test_generate_design_storm_schema_method_enum(registry: AgentToolRegistry) -> None:
    spec = registry._tools["generate_design_storm"]
    props = spec.parameters.get("properties", {})
    method_prop = props.get("method", {})
    assert "enum" in method_prop
    assert "chicago" in method_prop["enum"]
    assert "alternating_block" in method_prop["enum"]


# ---------------------------------------------------------------------------
# 3. Args mapper camelCase correctness
# ---------------------------------------------------------------------------

def test_generate_design_storm_mapper_chicago_cn(tmp_path: Path) -> None:
    import agentic_swmm.agent.tool_registry as reg_mod

    real_fn = reg_mod._repo_output_path

    def fake_repo_output(s: str):
        return tmp_path / s.lstrip("/")

    reg_mod._repo_output_path = fake_repo_output  # type: ignore[assignment]
    try:
        call = _call(
            "generate_design_storm",
            {
                "method": "chicago",
                "duration_min": 120.0,
                "out_json": "out/storm.json",
                "out_timeseries": "out/storm.txt",
                "form": "CN",
                "a1": 10.0,
                "c_coeff": 0.811,
                "b": 11.0,
                "n": 0.711,
                "return_period": 2.0,
                "dt": 5.0,
                "r": 0.4,
                "series_name": "TS_P2Y_120MIN",
            },
        )
        result = _generate_design_storm_args(call, _SESSION)
    finally:
        reg_mod._repo_output_path = real_fn  # type: ignore[assignment]

    assert result.get("method") == "chicago"
    assert result.get("duration") == 120.0
    assert "outJson" in result
    assert "outTimeseries" in result
    assert result.get("form") == "CN"
    assert result.get("a1") == 10.0
    assert result.get("cCoeff") == 0.811
    assert result.get("b") == 11.0
    assert result.get("n") == 0.711
    assert result.get("returnPeriod") == 2.0
    assert result.get("dt") == 5.0
    assert result.get("r") == 0.4
    assert result.get("seriesName") == "TS_P2Y_120MIN"


def test_generate_design_storm_mapper_alternating_block(tmp_path: Path) -> None:
    import agentic_swmm.agent.tool_registry as reg_mod

    real_fn = reg_mod._repo_output_path

    def fake_repo_output(s: str):
        return tmp_path / s.lstrip("/")

    reg_mod._repo_output_path = fake_repo_output  # type: ignore[assignment]
    try:
        call = _call(
            "generate_design_storm",
            {
                "method": "alternating_block",
                "duration_min": 60.0,
                "out_json": "out/storm_ab.json",
                "out_timeseries": "out/storm_ab.dat",
                "idf_json": '[{"duration_min":5,"intensity_mm_per_hr":60}]',
            },
        )
        result = _generate_design_storm_args(call, _SESSION)
    finally:
        reg_mod._repo_output_path = real_fn  # type: ignore[assignment]

    assert result.get("method") == "alternating_block"
    assert result.get("duration") == 60.0
    assert result.get("idfJson") == '[{"duration_min":5,"intensity_mm_per_hr":60}]'
    assert "outJson" in result
    assert "outTimeseries" in result


def test_generate_design_storm_mapper_generic_form(tmp_path: Path) -> None:
    import agentic_swmm.agent.tool_registry as reg_mod

    real_fn = reg_mod._repo_output_path

    def fake_repo_output(s: str):
        return tmp_path / s.lstrip("/")

    reg_mod._repo_output_path = fake_repo_output  # type: ignore[assignment]
    try:
        call = _call(
            "generate_design_storm",
            {
                "method": "chicago",
                "duration_min": 60.0,
                "out_json": "out/storm_gen.json",
                "out_timeseries": "out/storm_gen.txt",
                "form": "generic",
                "a_coeff": 700.0,
                "b": 10.0,
                "c_exp": 0.65,
            },
        )
        result = _generate_design_storm_args(call, _SESSION)
    finally:
        reg_mod._repo_output_path = real_fn  # type: ignore[assignment]

    assert result.get("form") == "generic"
    assert result.get("aCoeff") == 700.0
    assert result.get("b") == 10.0
    assert result.get("cExp") == 0.65


# ---------------------------------------------------------------------------
# 4. Missing required args return _failure (ok=False)
# ---------------------------------------------------------------------------

def test_missing_method_returns_failure() -> None:
    call = _call(
        "generate_design_storm",
        {"duration_min": 120.0, "out_json": "out/s.json", "out_timeseries": "out/s.txt"},
    )
    result = _generate_design_storm_args(call, _SESSION)
    assert result.get("ok") is False
    error_text = result.get("error", "") or result.get("summary", "")
    assert "method" in error_text


def test_missing_duration_min_returns_failure() -> None:
    call = _call(
        "generate_design_storm",
        {"method": "chicago", "out_json": "out/s.json", "out_timeseries": "out/s.txt"},
    )
    result = _generate_design_storm_args(call, _SESSION)
    assert result.get("ok") is False
    error_text = result.get("error", "") or result.get("summary", "")
    assert "duration_min" in error_text


def test_missing_out_json_returns_failure() -> None:
    call = _call(
        "generate_design_storm",
        {"method": "chicago", "duration_min": 120.0, "out_timeseries": "out/s.txt"},
    )
    result = _generate_design_storm_args(call, _SESSION)
    assert result.get("ok") is False
    error_text = result.get("error", "") or result.get("summary", "")
    assert "out_json" in error_text


def test_missing_out_timeseries_returns_failure() -> None:
    call = _call(
        "generate_design_storm",
        {"method": "chicago", "duration_min": 120.0, "out_json": "out/s.json"},
    )
    result = _generate_design_storm_args(call, _SESSION)
    assert result.get("ok") is False
    error_text = result.get("error", "") or result.get("summary", "")
    assert "out_timeseries" in error_text


# ---------------------------------------------------------------------------
# 5. ExpectedBinding row in mcp_coverage
# ---------------------------------------------------------------------------

def test_expected_binding_in_mcp_coverage() -> None:
    from agentic_swmm.agent.mcp_coverage import EXPECTED_BINDINGS

    names = {b.tool_spec_name for b in EXPECTED_BINDINGS}
    assert "generate_design_storm" in names


def test_expected_binding_mcp_details() -> None:
    from agentic_swmm.agent.mcp_coverage import EXPECTED_BINDINGS

    binding = next(b for b in EXPECTED_BINDINGS if b.tool_spec_name == "generate_design_storm")
    assert binding.mcp_server == "swmm-climate"
    assert binding.mcp_tool_name == "generate_design_storm"
    assert "design_storm.py" in binding.script_relpath


# ---------------------------------------------------------------------------
# 6. _DETERMINISTIC_BINDINGS entry
# ---------------------------------------------------------------------------

def test_generate_design_storm_in_deterministic_bindings() -> None:
    from agentic_swmm.agent.skill_router import _DETERMINISTIC_BINDINGS

    assert "generate_design_storm" in _DETERMINISTIC_BINDINGS
    assert _DETERMINISTIC_BINDINGS["generate_design_storm"] == "swmm-climate"


# ---------------------------------------------------------------------------
# 7. SkillRouter exposes generate_design_storm under swmm-climate
# ---------------------------------------------------------------------------

def test_skill_router_swmm_climate_contains_generate_design_storm(
    registry: AgentToolRegistry,
) -> None:
    from agentic_swmm.agent.skill_router import SkillRouter

    router = SkillRouter(registry)
    bundle = router.tools_for("swmm-climate")
    assert "generate_design_storm" in bundle.tool_names()
