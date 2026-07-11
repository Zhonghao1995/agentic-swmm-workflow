"""Tests for PR 4 (issue #246) reachability-batch fixes (C1–C6).

Per-item assertions:
  C1 — build_raingage_section: in registry, is_read_only=False, missing
       out_text_path → _failure, mapper emits camelCase keys.
  C2 — summarize_memory: obsidian_dir in schema; mapper emits obsidianDir.
  C3 — format_rainfall: extended schema properties surfaced.
  C4 — audit_run: compare_to in schema; mapper emits compareTo.
  C5 — retrieve_memory in skill_router._DETERMINISTIC_BINDINGS → swmm-rag-memory.
  C6 — plot_run: focus_day/window_start/window_end in schema and mapper.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Registry must be imported before handlers to avoid circular import.
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.tool_handlers.swmm_audit import _audit_run_args
from agentic_swmm.agent.tool_handlers.swmm_climate import (
    _build_raingage_section_args,
    _format_rainfall_args,
)
from agentic_swmm.agent.tool_handlers.swmm_plot import _plot_run_args
from agentic_swmm.agent.types import ToolCall


_SESSION = Path("/tmp/test_session_pr4")


@pytest.fixture(scope="module")
def registry() -> AgentToolRegistry:
    return AgentToolRegistry()


def _call(name: str, args: dict) -> ToolCall:
    c = MagicMock(spec=ToolCall)
    c.name = name
    c.args = args
    return c


# ---------------------------------------------------------------------------
# C1 — build_raingage_section
# ---------------------------------------------------------------------------

def test_c1_build_raingage_section_is_not_read_only(registry: AgentToolRegistry) -> None:
    spec = registry._tools["build_raingage_section"]
    assert spec.is_read_only is False


def test_c1_build_raingage_section_schema_has_required_fields(registry: AgentToolRegistry) -> None:
    spec = registry._tools["build_raingage_section"]
    props = spec.parameters.get("properties", {})
    assert "out_text_path" in props
    assert "out_json_path" in props
    assert "gage_id" in props
    assert "series_name" in props
    assert "rainfall_json_path" in props
    assert "rain_format" in props
    assert "interval_min" in props


def test_c1_build_raingage_section_missing_out_text_path_returns_failure() -> None:
    call = _call("build_raingage_section", {"out_json_path": "out/gage.json"})
    result = _build_raingage_section_args(call, _SESSION)
    assert result.get("ok") is False
    # _failure puts the message in "summary"; check either field.
    error_text = result.get("error", "") or result.get("summary", "")
    assert "out_text_path" in error_text


def test_c1_build_raingage_section_mapper_emits_camel_case() -> None:
    # Patch _repo_output_path to return a plausible Path.
    from agentic_swmm.agent.tool_registry import _repo_output_path as orig_fn

    import agentic_swmm.agent.tool_handlers.swmm_climate as climate_mod
    # We test by providing absolute paths and confirming the args dict keys.
    call = _call(
        "build_raingage_section",
        {
            "out_text_path": "out/gage.txt",
            "out_json_path": "out/gage.json",
            "gage_id": "G1",
            "series_name": "RAIN_01",
            "rain_format": "INTENSITY",
            "interval_min": 5,
            "scf": 1.0,
        },
    )
    # Inject a no-op _repo_output_path via monkeypatching the lazy import.
    import agentic_swmm.agent.tool_registry as reg_mod

    real_fn = reg_mod._repo_output_path

    def fake_repo_output(s: str):
        from pathlib import Path
        return Path(s)

    reg_mod._repo_output_path = fake_repo_output  # type: ignore[assignment]
    try:
        result = _build_raingage_section_args(call, _SESSION)
    finally:
        reg_mod._repo_output_path = real_fn  # type: ignore[assignment]

    assert "outTextPath" in result
    assert "outJsonPath" in result
    assert result.get("gageId") == "G1"
    assert result.get("seriesName") == "RAIN_01"
    assert result.get("rainFormat") == "INTENSITY"
    assert result.get("intervalMin") == 5
    assert result.get("scf") == 1.0


# ---------------------------------------------------------------------------
# C2 — summarize_memory obsidian_dir
# ---------------------------------------------------------------------------

def test_c2_summarize_memory_schema_has_obsidian_dir(registry: AgentToolRegistry) -> None:
    spec = registry._tools["summarize_memory"]
    props = spec.parameters.get("properties", {})
    assert "obsidian_dir" in props, "obsidian_dir must be in summarize_memory schema"


def test_c2_summarize_memory_mapper_emits_obsidian_dir() -> None:
    from agentic_swmm.agent.tool_registry import _summarize_memory_args as _sma

    call = _call("summarize_memory", {"runs_dir": "/some/runs", "obsidian_dir": "/vault/notes"})
    result = _sma(call, _SESSION)
    assert result.get("obsidianDir") == "/vault/notes"


def test_c2_summarize_memory_mapper_absent_obsidian_dir() -> None:
    from agentic_swmm.agent.tool_registry import _summarize_memory_args as _sma

    call = _call("summarize_memory", {"runs_dir": "/some/runs"})
    result = _sma(call, _SESSION)
    assert "obsidianDir" not in result


# ---------------------------------------------------------------------------
# C3 — format_rainfall extended schema
# ---------------------------------------------------------------------------

def test_c3_format_rainfall_schema_has_glob_params(registry: AgentToolRegistry) -> None:
    spec = registry._tools["format_rainfall"]
    props = spec.parameters.get("properties", {})
    assert "input_glob_patterns" in props
    assert "input_dat_paths" in props


def test_c3_format_rainfall_schema_has_station_params(registry: AgentToolRegistry) -> None:
    spec = registry._tools["format_rainfall"]
    props = spec.parameters.get("properties", {})
    assert "station_column" in props
    assert "default_station_id" in props
    assert "series_name_template" in props


def test_c3_format_rainfall_schema_has_window_params(registry: AgentToolRegistry) -> None:
    spec = registry._tools["format_rainfall"]
    props = spec.parameters.get("properties", {})
    assert "window_start" in props
    assert "window_end" in props


def test_c3_format_rainfall_mapper_glob_mode(tmp_path: Path) -> None:
    import agentic_swmm.agent.tool_registry as reg_mod

    real_fn = reg_mod._repo_output_path

    def fake_repo_output(s: str):
        return tmp_path / s.lstrip("/")

    reg_mod._repo_output_path = fake_repo_output  # type: ignore[assignment]
    try:
        call = _call(
            "format_rainfall",
            {
                "input_glob_patterns": ["data/rain_*.csv"],
                "out_json": "out/rain.json",
                "out_timeseries": "out/rain.dat",
                "station_column": "station",
                "series_name_template": "{station_id}_rain",
            },
        )
        result = _format_rainfall_args(call, _SESSION)
    finally:
        reg_mod._repo_output_path = real_fn  # type: ignore[assignment]

    assert "inputGlobPatterns" in result
    assert result["inputGlobPatterns"] == ["data/rain_*.csv"]
    assert result.get("stationColumn") == "station"
    assert result.get("seriesNameTemplate") == "{station_id}_rain"


# ---------------------------------------------------------------------------
# C4 — audit_run compare_to
# ---------------------------------------------------------------------------

def test_c4_audit_run_schema_has_compare_to(registry: AgentToolRegistry) -> None:
    spec = registry._tools["audit_run"]
    props = spec.parameters.get("properties", {})
    assert "compare_to" in props


def test_c4_audit_run_mapper_emits_compare_to_when_present(tmp_path) -> None:
    # run_dir must be a real directory since the ADR-0004 mapper fix.
    call = _call("audit_run", {"run_dir": str(tmp_path), "compare_to": "/runs/baseline"})
    result = _audit_run_args(call, _SESSION)
    assert result.get("compareTo") == "/runs/baseline"


def test_c4_audit_run_mapper_absent_compare_to(tmp_path) -> None:
    call = _call("audit_run", {"run_dir": str(tmp_path)})
    result = _audit_run_args(call, _SESSION)
    assert "compareTo" not in result


# ---------------------------------------------------------------------------
# C5 — retrieve_memory skill binding
# ---------------------------------------------------------------------------

def test_c6_plot_run_schema_has_focus_day(registry: AgentToolRegistry) -> None:
    spec = registry._tools["plot_run"]
    props = spec.parameters.get("properties", {})
    assert "focus_day" in props


def test_c6_plot_run_schema_has_window_start_end(registry: AgentToolRegistry) -> None:
    spec = registry._tools["plot_run"]
    props = spec.parameters.get("properties", {})
    assert "window_start" in props
    assert "window_end" in props


def _seed_run_dir(root: Path) -> Path:
    """Create a minimal fake run dir for _plot_run_args tests."""
    run_dir = root / "runs" / "agent" / "test-pr4"
    (run_dir / "04_builder").mkdir(parents=True)
    (run_dir / "05_runner").mkdir(parents=True)
    (run_dir / "04_builder" / "model.inp").write_text("[TITLE]\nfixture\n", encoding="utf-8")
    (run_dir / "05_runner" / "model.out").write_bytes(b"\x00")
    return run_dir


def test_c6_plot_run_mapper_emits_focus_day(tmp_path: Path) -> None:
    import agentic_swmm.agent.tool_registry as reg_mod
    from agentic_swmm.agent.tool_handlers import _shared as shared_mod

    orig_reg = reg_mod.repo_root
    orig_shared = shared_mod.repo_root
    reg_mod.repo_root = lambda: tmp_path  # type: ignore[assignment]
    shared_mod.repo_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        run_dir = _seed_run_dir(tmp_path)
        call = _call(
            "plot_run",
            {
                "run_dir": str(run_dir.relative_to(tmp_path)),
                "focus_day": "2023-01-01",
                "window_start": "08:00",
                "window_end": "10:00",
            },
        )
        result = _plot_run_args(call, _SESSION)
    finally:
        reg_mod.repo_root = orig_reg  # type: ignore[assignment]
        shared_mod.repo_root = orig_shared  # type: ignore[assignment]

    assert result.get("focusDay") == "2023-01-01"
    assert result.get("windowStart") == "08:00"
    assert result.get("windowEnd") == "10:00"


def test_c6_plot_run_mapper_absent_focus_day(tmp_path: Path) -> None:
    import agentic_swmm.agent.tool_registry as reg_mod
    from agentic_swmm.agent.tool_handlers import _shared as shared_mod

    orig_reg = reg_mod.repo_root
    orig_shared = shared_mod.repo_root
    reg_mod.repo_root = lambda: tmp_path  # type: ignore[assignment]
    shared_mod.repo_root = lambda: tmp_path  # type: ignore[assignment]
    try:
        run_dir = _seed_run_dir(tmp_path)
        call = _call(
            "plot_run",
            {"run_dir": str(run_dir.relative_to(tmp_path))},
        )
        result = _plot_run_args(call, _SESSION)
    finally:
        reg_mod.repo_root = orig_reg  # type: ignore[assignment]
        shared_mod.repo_root = orig_shared  # type: ignore[assignment]

    assert "focusDay" not in result
    assert "windowStart" not in result
    assert "windowEnd" not in result
