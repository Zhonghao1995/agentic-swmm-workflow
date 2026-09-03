"""The typed reference-free propagation tool (user decision 2026-09-02, F-55)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.agent.swmm_runtime import parameter_sweep
from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolCall
from agentic_swmm.utils.paths import repo_root

INP = "examples/tecnopolo/tecnopolo_r1_199401.inp"


def _fake_runner(inp: Path, sample_dir: Path, node: str) -> dict:
    text = inp.read_text(errors="ignore")
    imp = 0.0
    section = ""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            section = s.upper()
            continue
        if section == "[SUBCATCHMENTS]" and s and not s.startswith(";"):
            imp = float(s.split()[4])
            break
    return {"run_ok": True, "metrics": {"peak": {"node": node, "peak": round(0.02 + 0.001 * imp, 6), "units": "CMS", "time_hhmm": "03:15"}, "flow_units": "CMS"}}


@pytest.fixture
def fake_runner(monkeypatch):
    monkeypatch.setattr(parameter_sweep, "_default_runner", _fake_runner)


def test_the_tool_runs_a_sweep_and_reports_the_spread(fake_runner, tmp_path):
    registry = AgentToolRegistry()
    call = ToolCall(
        name="propagate_parameter_ranges",
        args={"inp_path": INP, "ranges": {"manning_n": [0.010, 0.020], "imperviousness": [60, 80]}, "run_dir": str(tmp_path / "run"), "node": "O1"},
    )
    result = registry.execute(call, tmp_path)
    assert result["ok"] is True, result
    assert result["node"] == "O1" and result["flow_units"] == "CMS"
    assert result["stats"]["samples_ok"] == 25 and result["stats"]["dominant_parameter"] == "pct_imperv"
    assert Path(result["summary_json"]).exists() and Path(result["summary_md"]).exists()
    assert "25/25 samples ran at O1" in result["summary"]
    visible = registry.output_for_model(result)
    for key in ("baseline_peak", "flow_units", "stats", "samples", "ranges", "evidence_boundary", "summary_json"):
        assert key in visible, key


def test_bad_ranges_fail_with_a_hint(fake_runner, tmp_path):
    registry = AgentToolRegistry()
    result = registry.execute(ToolCall(name="propagate_parameter_ranges", args={"inp_path": INP, "ranges": {"porosity": [0.1, 0.2]}}), tmp_path)
    assert result["ok"] is False and "unknown parameter" in result["summary"] and "aliases" in result["hint"]


def test_a_missing_inp_is_refused(fake_runner, tmp_path):
    registry = AgentToolRegistry()
    result = registry.execute(ToolCall(name="propagate_parameter_ranges", args={"inp_path": "runs/nope/model.inp", "ranges": {"n_imperv": [0.01, 0.02]}}), tmp_path)
    assert result["ok"] is False and "INP not found" in result["summary"]


def test_the_tool_is_registered_and_not_read_only():
    registry = AgentToolRegistry()
    assert "propagate_parameter_ranges" in registry.names
    assert registry.is_read_only("propagate_parameter_ranges") is False
    schema = registry.schemas({"propagate_parameter_ranges"})[0]["parameters"]
    assert schema["required"] == ["inp_path", "ranges"]
