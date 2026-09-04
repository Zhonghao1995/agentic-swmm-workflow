"""Climate-forcing scenario batches (ADR-0010)."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from agentic_swmm.agent.swmm_runtime import climate_scenarios as cs
from agentic_swmm.utils.paths import repo_root

INP_TEXT = """\
[TITLE]
climate unit fixture
[RAINGAGES]
RG1  INTENSITY  0:05  1.0  TIMESERIES  TS_RAIN
RG2  VOLUME     0:05  1.0  FILE  "rain.dat"  STA1  MM
[TIMESERIES]
;;rain series
TS_RAIN  06/01/2024  00:00  0.5
TS_RAIN  06/01/2024  00:05  1.0  ;peak
TS_OTHER 06/01/2024  00:00  9.9
[JUNCTIONS]
J1  1  1
[OUTFALLS]
OF1  0  FREE
"""

DAT_TEXT = """\
; station rain
STA1  2024  06  01  00  00  0.5
STA1  2024  06  01  00  05  1.0
"""


class TestParseFactors:
    def test_names_and_values(self):
        scenarios = cs.parse_factors("1.0, 1.1,1.35")
        assert [(s.name, s.precip_factor) for s in scenarios] == [
            ("baseline", 1.0),
            ("plus10", 1.1),
            ("plus35", 1.35),
        ]

    def test_minus_and_errors(self):
        assert cs.parse_factors("0.8")[0].name == "minus20"
        with pytest.raises(ValueError):
            cs.parse_factors("")
        with pytest.raises(ValueError):
            cs.parse_factors("-1.0")
        with pytest.raises(ValueError):
            cs.parse_factors("abc")


class TestScaling:
    def test_rain_sources_finds_series_and_files(self):
        sources = cs.rain_sources(INP_TEXT)
        assert sources.series_names == frozenset({"TS_RAIN"})
        assert sources.file_paths == ("rain.dat",)

    def test_only_named_series_rows_scale(self):
        scaled = cs.scale_timeseries(INP_TEXT, 2.0, frozenset({"TS_RAIN"}))
        assert "TS_RAIN  06/01/2024  00:00  1" in scaled
        assert "TS_RAIN  06/01/2024  00:05  2 ;peak" in scaled
        # Non-rain series and other sections stay byte-identical content.
        assert "TS_OTHER 06/01/2024  00:00  9.9" in scaled
        assert "OF1  0  FREE" in scaled
        assert ";;rain series" in scaled

    def test_dat_scaling_keeps_comments(self):
        scaled = cs.scale_dat_text(DAT_TEXT, 1.2)
        assert "; station rain" in scaled
        assert scaled.splitlines()[1].endswith("0.6")
        assert scaled.splitlines()[2].endswith("1.2")

    def test_write_scenario_inp_scales_file_reference_locally(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        inp = base / "model.inp"
        inp.write_text(INP_TEXT, encoding="utf-8")
        (base / "rain.dat").write_text(DAT_TEXT, encoding="utf-8")

        out_dir = tmp_path / "scenario"
        scenario_inp = cs.write_scenario_inp(inp, cs.ScenarioSpec("plus20", 1.2), out_dir)

        text = scenario_inp.read_text(encoding="utf-8")
        assert 'FILE  "rain.dat"' in text  # rewritten to the local basename
        local_dat = out_dir / "rain.dat"
        assert local_dat.is_file()
        assert local_dat.read_text(encoding="utf-8").splitlines()[2].endswith("1.2")


class TestBatch:
    def _fake_runner(self, results: dict[str, dict]):
        def runner(inp: Path, scenario_dir: Path, node: str) -> dict:
            name = scenario_dir.name
            return results.get(
                name,
                {
                    "run_ok": True,
                    "metrics": {
                        "peak": {"peak": 1.0, "time_hhmm": "01:00"},
                        "continuity": {
                            "runoff_quantity": {
                                "Total Precipitation": {"col1": 0.1, "col2": 2.4},
                                "Surface Runoff": {"col1": 0.03, "col2": 0.5},
                            },
                            "flow_routing": {
                                "Flooding Loss": {"col1": 0.0, "col2": 0.0},
                                "External Outflow": {"col1": 0.02, "col2": 0.2},
                            },
                            "continuity_error_percent": {"runoff_quantity": -0.2},
                        },
                    },
                },
            )

        return runner

    def test_batch_writes_summary_and_scenarios(self, tmp_path):
        inp = tmp_path / "model.inp"
        inp.write_text(INP_TEXT, encoding="utf-8")
        (tmp_path / "rain.dat").write_text(DAT_TEXT, encoding="utf-8")
        run_dir = tmp_path / "run"

        result = cs.run_climate_batch(
            base_inp=inp,
            run_dir=run_dir,
            scenarios=cs.parse_factors("1.0,1.2"),
            runner=self._fake_runner({}),
        )
        assert result.ok is True
        assert result.node == "OF1"  # first outfall, not the O1 literal
        scenarios_dirs = [d for d in (run_dir / "03_climate").glob("scenarios_*") if d.is_dir()]
        assert len(scenarios_dirs) == 1 and scenarios_dirs[0].name == "scenarios_f1+1.2"
        assert (scenarios_dirs[0] / "baseline" / "model.inp").is_file()
        assert (scenarios_dirs[0] / "plus20" / "model.inp").is_file()

        payload = json.loads(Path(result.summary_json).read_text(encoding="utf-8"))
        assert [s["name"] for s in payload["scenarios"]] == ["baseline", "plus20"]
        assert payload["scenarios"][0]["metrics"]["total_precip_depth"] == 2.4

        md = Path(result.summary_md).read_text(encoding="utf-8")
        assert "| baseline | x1 |" in md
        assert "| plus20 | x1.2 |" in md

    def test_failed_scenario_keeps_its_row(self, tmp_path):
        inp = tmp_path / "model.inp"
        inp.write_text(INP_TEXT, encoding="utf-8")
        (tmp_path / "rain.dat").write_text(DAT_TEXT, encoding="utf-8")

        result = cs.run_climate_batch(
            base_inp=inp,
            run_dir=tmp_path / "run",
            scenarios=cs.parse_factors("1.0,1.2"),
            runner=self._fake_runner(
                {"plus20": {"run_ok": False, "error": "solver exploded"}}
            ),
        )
        assert result.ok is False
        rows = {s.name: s for s in result.scenarios}
        assert rows["baseline"].run_ok is True
        assert rows["plus20"].run_ok is False
        assert "solver exploded" in rows["plus20"].error
        assert "FAILED" in Path(result.summary_md).read_text(encoding="utf-8")


class TestTypedTool:
    def test_missing_inp_fails_soft(self, tmp_path):
        from agentic_swmm.agent.tool_handlers.swmm_climate import run_climate_scenarios_tool
        from agentic_swmm.agent.types import ToolCall

        result = run_climate_scenarios_tool(
            ToolCall(name="run_climate_scenarios", args={}), tmp_path
        )
        assert result["ok"] is False
        assert "inp_path" in result["summary"] or "inp_path" in str(result)

    def test_tool_runs_batch_with_stubbed_runner(self, tmp_path, monkeypatch):
        from agentic_swmm.agent.tool_handlers.swmm_climate import run_climate_scenarios_tool
        from agentic_swmm.agent.types import ToolCall

        def fake_runner(inp: Path, scenario_dir: Path, node: str) -> dict:
            return {"run_ok": True, "metrics": {}}

        monkeypatch.setattr(cs, "_default_runner", fake_runner)

        run_dir = repo_root() / "runs" / "agent" / f"climate-tool-test-{uuid.uuid4().hex[:8]}"
        inp_dir = run_dir / "05_builder"
        inp_dir.mkdir(parents=True)
        inp = inp_dir / "model.inp"
        inp.write_text(INP_TEXT, encoding="utf-8")
        (inp_dir / "rain.dat").write_text(DAT_TEXT, encoding="utf-8")
        try:
            result = run_climate_scenarios_tool(
                ToolCall(
                    name="run_climate_scenarios",
                    args={"inp_path": str(inp), "run_dir": str(run_dir), "factors": "1.0,1.1"},
                ),
                tmp_path,
            )
            assert result["ok"] is True
            assert len(result["scenarios"]) == 2
            assert Path(result["summary_json"]).is_file()
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


@pytest.mark.skipif(shutil.which("swmm5") is None, reason="swmm5 binary not available on PATH")
class TestEndToEndWithSwmm5:
    def test_uplift_increases_precip_totals_on_the_example_case(self, tmp_path):
        # The scenario writer must self-contain every FILE-backed series
        # (rain scaled, temperature/evaporation copied verbatim), so the
        # example INP is used straight from examples/ with no manual
        # sidecar staging.
        inp = repo_root() / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp"

        result = cs.run_climate_batch(
            base_inp=inp,
            run_dir=tmp_path / "run",
            scenarios=cs.parse_factors("1.0,1.2"),
        )
        assert result.ok, [s.error for s in result.scenarios]
        baseline, plus20 = result.scenarios
        p0 = baseline.metrics.get("total_precip_depth")
        p1 = plus20.metrics.get("total_precip_depth")
        assert isinstance(p0, float) and isinstance(p1, float)
        assert p1 > p0 * 1.15, (p0, p1)
