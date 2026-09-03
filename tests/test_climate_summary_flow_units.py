"""The climate comparison carries the report's flow unit (F-62, F-52 class).

Live finding 2026-09-02 (scenario S06 r2): the LLM's peak table printed
"0.061" bare because run_climate_scenarios' summary had no unit to give it.
"""

from __future__ import annotations

from agentic_swmm.agent.swmm_runtime import climate_scenarios as cs


def _manifest(units):
    peak = {"node": "OU2", "peak": 0.061, "time_hhmm": "03:15"}
    if units:
        peak["units"] = units
    return {"metrics": {"peak": peak, "continuity": {}}}


def test_summary_metrics_carry_the_units():
    assert cs._summary_metrics(_manifest("CMS"))["flow_units"] == "CMS"


def test_summary_metrics_fall_back_to_metrics_flow_units():
    manifest = _manifest(None)
    manifest["metrics"]["flow_units"] = "LPS"
    assert cs._summary_metrics(manifest)["flow_units"] == "LPS"


def test_summary_metrics_say_none_for_an_older_manifest():
    assert cs._summary_metrics(_manifest(None))["flow_units"] is None


def _run(name, factor, metrics):
    return cs.ScenarioRun(name=name, precip_factor=factor, inp="x.inp", scenario_dir="d", run_ok=True, metrics=metrics, error="")


def test_markdown_header_names_the_unit():
    runs = [_run("baseline", 1.0, {"peak_flow": 0.061, "flow_units": "CMS"}), _run("p120", 1.2, {"peak_flow": 0.074, "flow_units": "CMS"})]
    md = cs._render_summary_md("OU2", runs)
    assert "peak flow (CMS)" in md


def test_markdown_header_is_honest_without_a_unit():
    runs = [_run("baseline", 1.0, {"peak_flow": 0.061})]
    assert "peak flow (flow units not recorded)" in cs._render_summary_md("OU2", runs)
