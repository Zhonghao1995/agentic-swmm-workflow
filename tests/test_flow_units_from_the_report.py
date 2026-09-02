"""The flow unit on every peak is the report's, never an assumption (F-52).

Live finding 2026-09-02 (scenario S13c, Seattle): the synthesized model runs
in LPS, the runner manifest carried no units, and the shell footer said
"Peak: 124.68 CMS" for 124.68 litres per second.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from agentic_swmm.agent.digest_render import _format_peak

REPO = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RPT_LPS = """
  *********************
  Analysis Options
  *********************
  Flow Units ............... LPS
  Process Models:
    Rainfall/Runoff ........ YES

  ***********************
  Node Inflow Summary
  ***********************

  -------------------------------------------------------------------------------------------------
                                  Maximum  Maximum                  Lateral       Total        Flow
                                  Lateral    Total  Time of Max      Inflow      Inflow     Balance
                                   Inflow   Inflow   Occurrence      Volume      Volume       Error
  Node                 Type           LPS      LPS  days hr:min    10^6 ltr    10^6 ltr     Percent
  -------------------------------------------------------------------------------------------------
  497_outfall          OUTFALL      0.000  124.680     0  00:15       0.000       0.312       0.000
"""


def test_runner_records_the_reports_flow_units(tmp_path):
    runner = _load("swmm_runner_f52", "skills/swmm-runner/scripts/swmm_runner.py")
    rpt = tmp_path / "model.rpt"
    rpt.write_text(RPT_LPS)
    assert runner.flow_units_from_rpt_text(RPT_LPS) == "LPS"
    peak = runner.parse_peak_from_rpt(rpt, "497_outfall")
    assert peak["peak"] == 124.68
    assert peak["units"] == "LPS"


def test_runner_leaves_units_none_when_the_report_has_no_header():
    runner = _load("swmm_runner_f52b", "skills/swmm-runner/scripts/swmm_runner.py")
    assert runner.flow_units_from_rpt_text("no options block here") is None


def test_digest_prints_the_recorded_unit():
    payload = {"metrics": {"peak": {"node": "497_outfall", "peak": 124.68, "units": "LPS", "time_hhmm": "00:15"}}}
    assert _format_peak(payload) == "Peak: 124.68 LPS @ 00:15 at 497_outfall"


def test_digest_falls_back_to_the_metrics_flow_units():
    payload = {"metrics": {"flow_units": "CFS", "peak": {"node": "O1", "peak": 3.2, "time_hhmm": "01:00"}}}
    assert _format_peak(payload) == "Peak: 3.2 CFS @ 01:00 at O1"


def test_digest_never_assumes_cms_for_an_older_manifest():
    payload = {"metrics": {"peak": {"node": "O1", "peak": 0.061, "time_hhmm": "03:15"}}}
    line = _format_peak(payload)
    assert "CMS" not in line
    assert line == "Peak: 0.061 (flow units not recorded) @ 03:15 at O1"


def test_audit_reads_the_unit_from_the_report(tmp_path):
    audit = _load("audit_run_f52", "skills/swmm-experiment-audit/scripts/audit_run.py")
    rpt = tmp_path / "model.rpt"
    rpt.write_text(RPT_LPS)
    parsed = audit.parse_node_inflow_peak(rpt, "497_outfall")
    assert parsed["value"] == 124.68
    assert parsed["unit"] == "LPS"


def test_memory_context_does_not_invent_a_unit():
    from agentic_swmm.agent import memory_context

    src = Path(memory_context.__file__).read_text(encoding="utf-8")
    assert 'or "CMS"' not in src
