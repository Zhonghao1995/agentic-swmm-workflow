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
    sweep_dirs = [d for d in (tmp_path / "run" / "09_audit").glob("parameter_sweep_*") if d.is_dir()]
    assert any((d / "s01" / "model.inp").exists() for d in sweep_dirs)
    # F-108: the files are named after the swept parameters.
    assert Path(result.summary_md).name == "parameter_sweep_n_imperv+pct_imperv.md"


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


def test_two_sweeps_in_one_run_keep_their_own_files() -> None:
    """Live finding F-108 (2026-09-03, S48): five sweeps overwrote one summary."""
    from agentic_swmm.agent.swmm_runtime.parameter_sweep import sweep_tag

    assert sweep_tag({"pct_imperv": (50, 90)}) == "pct_imperv"
    assert sweep_tag({"n_imperv": (0.01, 0.02), "pct_imperv": (60, 80)}) == "n_imperv+pct_imperv"
    assert sweep_tag({}) == "all"


def test_one_at_a_time_mode_ranks_the_parameters_in_one_call(tmp_path: Path) -> None:
    """Live finding F-109 (2026-09-03, S48 r2): a reference-free ranking needs each parameter varied alone."""
    from agentic_swmm.agent.swmm_runtime import parameter_sweep as ps

    base = tmp_path / "model.inp"
    base.write_text(_MINI_INP if "_MINI_INP" in globals() else "[TITLE]\nmini\n[OUTFALLS]\nO1 0 FREE\n", encoding="utf-8")

    def runner(inp, sample_dir, node):
        text = inp.read_text(encoding="utf-8", errors="ignore")
        peak = 0.05
        # pct_imperv moves the peak; n_imperv does not (a stand-in for a real response surface).
        for line in text.splitlines():
            if line.strip().startswith("S") and "pct" in line.lower():
                pass
        # Read the sampled values back from the sample folder name via sample_dir; the sweep
        # writes them into the INP, but for the test the response is keyed by the sample name.
        name = sample_dir.name
        if name.startswith("pct_imperv_s"):
            idx = int(name.rsplit("s", 1)[1])
            peak = 0.05 + 0.004 * idx
        return {"run_ok": True, "metrics": {"peak": {"peak": peak, "units": "CMS", "node": node}}, "node_selection": {"resolved": "O1"}}

    result = ps.run_parameter_sweep(
        base_inp=base, run_dir=tmp_path / "run", ranges={"n_imperv": (0.01, 0.02), "pct_imperv": (60, 80)},
        node="O1", n_samples=4, runner=runner, mode="one_at_a_time",
    )
    assert result.ok
    assert result.stats["mode"] == "one_at_a_time"
    assert result.stats["ranking"] == ["pct_imperv", "n_imperv"]
    assert result.stats["dominant_parameter"] == "pct_imperv"
    assert result.stats["per_parameter"]["n_imperv"]["peak_spread"] == 0
    assert Path(result.summary_md).name == "parameter_sweep_oat_n_imperv+pct_imperv.md"
    md = Path(result.summary_md).read_text(encoding="utf-8")
    assert "One-at-a-time ranking" in md and "| 1 | pct_imperv |" in md
    assert (tmp_path / "run" / "09_audit" / "parameter_sweep_oat_n_imperv+pct_imperv" / "pct_imperv_s01" / "model.inp").exists()


def test_one_at_a_time_levels_are_per_parameter_and_capped(tmp_path: Path) -> None:
    """Live finding F-110 (2026-09-03, S48 r3): n_samples=25 per parameter cost 150 runs."""
    from agentic_swmm.agent.swmm_runtime import parameter_sweep as ps

    base = tmp_path / "model.inp"
    base.write_text("[TITLE]\nmini\n[OUTFALLS]\nO1 0 FREE\n", encoding="utf-8")
    calls: list[str] = []

    def runner(inp, sample_dir, node):
        calls.append(sample_dir.name)
        return {"run_ok": True, "metrics": {"peak": {"peak": 0.1, "units": "CMS", "node": node}}, "node_selection": {"resolved": "O1"}}

    ps.run_parameter_sweep(
        base_inp=base, run_dir=tmp_path / "run", ranges={"n_imperv": (0.01, 0.02), "pct_imperv": (60, 80)},
        node="O1", n_samples=25, runner=runner, mode="one_at_a_time",
    )
    # baseline + 9 levels x 2 parameters, not 25 x 2
    assert len(calls) == 1 + 2 * ps.OAT_MAX_LEVELS


def test_swmm_depression_storage_names_are_aliases() -> None:
    from agentic_swmm.agent.swmm_runtime import parameter_sweep as ps

    parsed = ps.parse_ranges({"dstore_imperv": [1, 3], "dstore_perv": [2, 6]})
    assert set(parsed) == {"s_imperv", "s_perv"}
