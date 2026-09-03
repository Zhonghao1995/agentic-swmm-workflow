"""Global parameter-range propagation (user decision 2026-09-02, F-55)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic_swmm.agent.swmm_runtime import parameter_sweep as ps

INP = """[TITLE]
t
[OPTIONS]
FLOW_UNITS CMS
[RAINGAGES]
G1 INTENSITY 1:00 1.0 TIMESERIES R1
[SUBCATCHMENTS]
S1 G1 J1 5 40 100 0.5 0
S2 G1 J1 3 55 80 0.5 0 ; comment kept
[SUBAREAS]
S1 0.013 0.2 1.5 4 25 OUTLET
S2 0.015 0.25 1.5 4 25 OUTLET
[CONDUITS]
C1 J1 O1 100 0.013 0 0 0 0
[TIMESERIES]
R1 0:00 5
[OUTFALLS]
O1 0 FREE
"""


def test_aliases_and_ranges_parse_from_text_and_mappings():
    assert ps.parse_ranges("manning_n=0.010,0.020; imperviousness=80,60") == {"n_imperv": (0.01, 0.02), "pct_imperv": (60.0, 80.0)}
    assert ps.parse_ranges({"conduit_roughness": [0.011, 0.015]}) == {"conduit_n": (0.011, 0.015)}
    with pytest.raises(ValueError):
        ps.parse_ranges({"porosity": [0.1, 0.2]})


def test_small_grids_are_factorial_and_large_ones_latin_hypercube():
    two = ps.sample_space({"n_imperv": (0.01, 0.02), "pct_imperv": (60, 80)})
    assert len(two) == 25 and {round(s["n_imperv"], 4) for s in two} == {0.01, 0.0125, 0.015, 0.0175, 0.02}
    four = ps.sample_space({"n_imperv": (0.01, 0.02), "pct_imperv": (60, 80), "conduit_n": (0.011, 0.015), "s_imperv": (1, 3)})
    assert len(four) == ps.MAX_FACTORIAL
    assert all(0.01 <= s["n_imperv"] <= 0.02 for s in four)
    assert ps.sample_space({"n_imperv": (0.01, 0.02)}, 7, seed=1) == ps.sample_space({"n_imperv": (0.01, 0.02)}, 7, seed=1)


def test_rewrite_sets_every_object_and_keeps_comments():
    out = ps.rewrite_inp(INP, {"n_imperv": 0.02, "pct_imperv": 70, "conduit_n": 0.012})
    assert "S1 G1 J1 5 70 100 0.5 0" in out and "S2 G1 J1 3 70 80 0.5 0 ; comment kept" in out
    assert "S1 0.02 0.2 1.5 4 25 OUTLET" in out and "S2 0.02 0.25 1.5 4 25 OUTLET" in out
    assert "C1 J1 O1 100 0.012 0 0 0 0" in out
    assert "[TIMESERIES]\nR1 0:00 5" in out


def _fake_runner(inp: Path, sample_dir: Path, node: str) -> dict:
    text = inp.read_text()
    n = float([l for l in text.splitlines() if l.startswith("S1 0.")][0].split()[1])
    imp = float([l for l in text.splitlines() if l.startswith("S1 G1")][0].split()[4])
    peak = round(0.05 + 0.002 * imp - 1.0 * n, 6)
    return {"run_ok": True, "metrics": {"peak": {"node": node, "peak": peak, "units": "CMS", "time_hhmm": "01:00"}, "flow_units": "CMS"}}


def test_the_sweep_writes_the_audit_artifacts_and_finds_the_dominant_parameter(tmp_path):
    base = tmp_path / "model.inp"
    base.write_text(INP)
    result = ps.run_parameter_sweep(
        base_inp=base, run_dir=tmp_path / "run", ranges={"n_imperv": (0.01, 0.02), "pct_imperv": (60, 80)}, node="O1", runner=_fake_runner
    )
    assert result.ok and result.node == "O1" and result.flow_units == "CMS"
    assert len(result.samples) == 25 and all(s.run_ok for s in result.samples)
    assert result.stats["dominant_parameter"] == "pct_imperv"
    assert result.stats["peak_max"] > result.stats["peak_min"]
    payload = json.loads(Path(result.summary_json).read_text())
    assert payload["node"] == "O1" and len(payload["samples"]) == 25 and "evidence_boundary" in payload
    assert Path(result.summary_md).read_text().startswith("# Parameter range propagation")
    assert (tmp_path / "run" / "09_audit" / "parameter_sweep" / "s01" / "model.inp").exists()


def test_a_failed_sample_is_carried_not_dropped(tmp_path):
    base = tmp_path / "model.inp"
    base.write_text(INP)
    calls = {"n": 0}

    def flaky(inp, sample_dir, node):
        calls["n"] += 1
        if calls["n"] == 3:
            return {"run_ok": False, "error": "swmm5 exploded"}
        return _fake_runner(inp, sample_dir, node)

    result = ps.run_parameter_sweep(base_inp=base, run_dir=tmp_path / "run", ranges={"n_imperv": (0.01, 0.02)}, node="O1", n_samples=4, runner=flaky)
    assert result.stats["samples_failed"] == 1 and result.stats["samples_ok"] == 3
    failed = [s for s in result.samples if not s.run_ok]
    assert failed and "exploded" in failed[0].error


def _auto_runner_factory(seen: list[str]):
    def runner(inp: Path, sample_dir: Path, node: str) -> dict:
        seen.append(node)
        resolved = "OUT_BIG" if node == "auto" else node
        manifest = _fake_runner(inp, sample_dir, resolved)
        manifest["node_selection"] = {"requested": node, "resolved": resolved, "rule": "outfall carrying the largest total volume"}
        return manifest

    return runner


def test_without_a_node_the_sweep_reports_at_the_runners_dominant_outfall(tmp_path):
    # Live finding F-72 (2026-09-02): the INP's first outfall is not the run's dominant one.
    base = tmp_path / "model.inp"
    base.write_text(INP)
    seen: list[str] = []
    result = ps.run_parameter_sweep(base_inp=base, run_dir=tmp_path / "run", ranges={"n_imperv": (0.01, 0.02)}, n_samples=3, runner=_auto_runner_factory(seen))
    assert result.node == "OUT_BIG"
    assert seen[0] == "auto" and set(seen[1:]) == {"OUT_BIG"}
    assert json.loads(Path(result.summary_json).read_text())["node"] == "OUT_BIG"


def test_an_explicit_node_is_used_as_given(tmp_path):
    base = tmp_path / "model.inp"
    base.write_text(INP)
    seen: list[str] = []
    result = ps.run_parameter_sweep(base_inp=base, run_dir=tmp_path / "run", ranges={"n_imperv": (0.01, 0.02)}, node="J9", n_samples=2, runner=_auto_runner_factory(seen))
    assert result.node == "J9" and set(seen) == {"J9"}


def test_the_climate_batch_locks_the_dominant_outfall_too(tmp_path):
    from agentic_swmm.agent.swmm_runtime import climate_scenarios as cs

    base = tmp_path / "model.inp"
    base.write_text(INP)
    seen: list[str] = []
    result = cs.run_climate_batch(base_inp=base, run_dir=tmp_path / "run", scenarios=(cs.ScenarioSpec("baseline", 1.0), cs.ScenarioSpec("plus20", 1.2)), runner=_auto_runner_factory(seen))
    assert result.node == "OUT_BIG"
    assert seen == ["auto", "OUT_BIG"]
