"""The default report node is the outfall that matters, chosen after the run.

Live finding F-02 (2026-09-02, pty-driven sessions S01/S02/S03): every
default (``aiswmm run``, the ``run_swmm_inp`` tool, the MCP server,
``inspect_plot_options`` and ``aiswmm plot``) picked "the INP's first
outfall". On real multi-outfall municipal networks that outfall was dry or
trivial: the digest said ``Peak: 0.0 CMS @ 00:00 at DOF007052`` while
``OUT_DMH002395`` carried 2.7 ML, and the flat-line hydrograph at the dry
outfall went into the client's Word report.

The default is now ``auto``: resolved AFTER the run to the outfall carrying
the largest total volume in the Outfall Loading Summary (tie-break on max
flow), falling back to the first ``[OUTFALLS]`` entry when the report has no
outfall rows. Every surface asks the same helper, and each one says which
rule chose the node.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentic_swmm.agent.swmm_runtime.rpt_summary import dominant_outfall
from agentic_swmm.agent.swmm_runtime.run_artifacts import find_rpt, preferred_report_node
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root
from agentic_swmm.utils.subprocess_runner import CommandResult

REPO = repo_root()
RUNNER_SCRIPT = REPO / "skills" / "swmm-runner" / "scripts" / "swmm_runner.py"

INP = """\
[TITLE]
two outfalls, the first one dry

[JUNCTIONS]
;;Name  Elevation  MaxDepth  InitDepth  SurDepth  Aponded
J1      1.0        2.0       0          0         0

[OUTFALLS]
;;Name    Elevation  Type  Stage  Gated  RouteTo
OUT_A     0.0        FREE         NO
OUT_B     0.0        FREE         NO

[CONDUITS]
C1  J1  OUT_B  100  0.013  0  0  0  0
"""

RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)

  *******************
  Node Inflow Summary
  *******************

  -------------------------------------------------------------------------------------------------
                                  Maximum  Maximum                  Lateral       Total        Flow
                                  Lateral    Total  Time of Max      Inflow      Inflow     Balance
                                   Inflow   Inflow   Occurrence      Volume      Volume       Error
  Node                 Type           CMS      CMS  days hr:min    10^6 ltr    10^6 ltr     Percent
  -------------------------------------------------------------------------------------------------
  J1                   JUNCTION     0.100    0.100     0  00:20       0.500       0.500       0.000
  OUT_A                OUTFALL      0.000    0.000     0  00:00       0.000       0.000       0.000
  OUT_B                OUTFALL      0.000    0.098     0  00:21       0.000       0.495       0.000


  ***********************
  Outfall Loading Summary
  ***********************

  -----------------------------------------------------------
                         Flow       Avg       Max       Total
                         Freq      Flow      Flow      Volume
  Outfall Node           Pcnt       CMS       CMS    10^6 ltr
  -----------------------------------------------------------
  OUT_A                  0.00     0.000     0.000       0.000
  OUT_B                 73.68     0.046     0.098       0.495
  -----------------------------------------------------------
  System                36.84     0.046     0.098       0.495

"""

DRY_RPT = """\
  ***********************
  Outfall Loading Summary
  ***********************

  No outfall loading occurred.

  *****************
  Link Flow Summary
  *****************
"""


def _load_runner_script():
    spec = importlib.util.spec_from_file_location("swmm_runner_f02", RUNNER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def run_dir():
    # Handlers only accept run dirs inside the repository sandbox.
    scratch = REPO / "runs" / "_test_dominant_outfall"
    shutil.rmtree(scratch, ignore_errors=True)
    run = scratch / "run"
    (run / "05_builder").mkdir(parents=True)
    (run / "06_runner").mkdir(parents=True)
    (run / "05_builder" / "model.inp").write_text(INP, encoding="utf-8")
    (run / "06_runner" / "model.rpt").write_text(RPT, encoding="utf-8")
    (run / "06_runner" / "model.out").write_bytes(b"")
    (run / "06_runner" / "manifest.json").write_text(json.dumps({
        "inp": str(run / "05_builder" / "model.inp"),
        "files": {"rpt": str(run / "06_runner" / "model.rpt"), "out": str(run / "06_runner" / "model.out")},
    }), encoding="utf-8")
    yield run
    shutil.rmtree(scratch, ignore_errors=True)


class TestDominantOutfall:
    def test_largest_total_volume_wins(self):
        assert dominant_outfall(RPT) == "OUT_B"

    def test_ties_break_on_max_flow(self):
        tied = RPT.replace("OUT_A                  0.00     0.000     0.000       0.000",
                           "OUT_A                 50.00     0.010     0.150       0.495")
        assert dominant_outfall(tied) == "OUT_A"

    def test_no_rows_means_none(self):
        assert dominant_outfall(DRY_RPT) is None
        assert dominant_outfall("") is None


class TestPreferredReportNode:
    def test_the_run_report_decides_once_it_exists(self, run_dir):
        manifest = json.loads((run_dir / "06_runner" / "manifest.json").read_text())
        node, reason = preferred_report_node(run_dir, manifest, run_dir / "05_builder" / "model.inp")
        assert node == "OUT_B"
        assert "largest total volume" in reason

    def test_find_rpt_reads_the_manifest_then_the_runner_stage(self, run_dir):
        manifest = json.loads((run_dir / "06_runner" / "manifest.json").read_text())
        assert find_rpt(run_dir, manifest) == run_dir / "06_runner" / "model.rpt"
        assert find_rpt(run_dir, {}) == run_dir / "06_runner" / "model.rpt"

    def test_without_a_report_the_inp_first_outfall_remains(self, run_dir):
        (run_dir / "06_runner" / "model.rpt").unlink()
        node, reason = preferred_report_node(run_dir, {}, run_dir / "05_builder" / "model.inp")
        assert node == "OUT_A"
        assert "first outfall" in reason


class TestAgentToolDefault:
    def test_run_swmm_inp_asks_for_auto_when_no_node_is_given(self, tmp_path, monkeypatch):
        import agentic_swmm.agent.tool_registry as tr
        from agentic_swmm.agent.tool_handlers import swmm_runner
        from agentic_swmm.agent.tool_handlers.swmm_runner import _run_swmm_inp_args

        inp = tmp_path / "model.inp"
        inp.write_text(INP, encoding="utf-8")
        monkeypatch.setattr(tr, "_resolve_inp_for_run", lambda call: inp)
        monkeypatch.setattr(swmm_runner, "_resolve_or_create_run_dir", lambda call, key: tmp_path / "run")

        mapped = _run_swmm_inp_args(ToolCall("run_swmm_inp", {"inp_path": str(inp)}), tmp_path)
        assert mapped["node"] == "auto"

        explicit = _run_swmm_inp_args(ToolCall("run_swmm_inp", {"inp_path": str(inp), "node": "J1"}), tmp_path)
        assert explicit["node"] == "J1"

    def test_inspect_plot_options_leads_with_the_dominant_outfall(self, run_dir):
        from agentic_swmm.agent.tool_handlers.swmm_plot import _inspect_plot_options_tool

        call = ToolCall("inspect_plot_options", {"run_dir": str(run_dir.relative_to(REPO))})
        payload = _inspect_plot_options_tool(call, REPO)
        assert payload["ok"] is True, payload
        results = payload["results"]
        assert results["node_options"][0] == "OUT_B"
        assert results["defaults"]["node"] == "OUT_B"
        assert "largest total volume" in results["defaults"]["node_reason"]


class TestPlotCli:
    def test_aiswmm_plot_defaults_to_the_dominant_outfall(self, run_dir, monkeypatch):
        from agentic_swmm.commands import plot as plot_cmd

        seen: list[list[str]] = []

        def fake_run(command, *, check=True):
            seen.append(list(command))
            return CommandResult(command=list(command), return_code=0, started_at_utc="", finished_at_utc="", stdout="", stderr="")

        monkeypatch.setattr(plot_cmd, "run_command", fake_run)
        args = Namespace(
            run_dir=run_dir, inp=None, out_file=None, out_png=None,
            rain_ts="rain", rain_kind="depth_mm_per_dt", node_attr="Total_inflow",
            pad_hours=0, width=None, link=None, node=None,
            focus_day=None, window_start=None, window_end=None,
        )
        assert plot_cmd.main(args) == 0
        assert seen, "plot script was not invoked"
        command = seen[0]
        assert command[command.index("--node") + 1] == "OUT_B"


class TestRunnerScript:
    def test_auto_resolves_after_the_run(self, tmp_path):
        mod = _load_runner_script()
        rpt = tmp_path / "model.rpt"
        rpt.write_text(RPT, encoding="utf-8")
        inp = tmp_path / "model.inp"
        inp.write_text(INP, encoding="utf-8")
        assert mod.dominant_outfall_from_rpt(rpt) == "OUT_B"
        node, rule = mod.resolve_report_node("auto", rpt, inp)
        assert node == "OUT_B"
        assert "largest total volume" in rule

    def test_a_requested_node_is_never_overridden(self, tmp_path):
        mod = _load_runner_script()
        rpt = tmp_path / "model.rpt"
        rpt.write_text(RPT, encoding="utf-8")
        assert mod.resolve_report_node("J1", rpt, None) == ("J1", "requested")

    def test_a_dry_report_falls_back_to_the_inp(self, tmp_path):
        mod = _load_runner_script()
        rpt = tmp_path / "model.rpt"
        rpt.write_text(DRY_RPT, encoding="utf-8")
        inp = tmp_path / "model.inp"
        inp.write_text(INP, encoding="utf-8")
        node, rule = mod.resolve_report_node("auto", rpt, inp)
        assert node == "OUT_A"
        assert "first [OUTFALLS]" in rule

    def test_the_peak_metric_follows_the_resolved_node(self, tmp_path):
        mod = _load_runner_script()
        rpt = tmp_path / "model.rpt"
        rpt.write_text(RPT, encoding="utf-8")
        peak = mod.parse_peak_from_rpt(rpt, "OUT_B")
        assert peak["peak"] == 0.098 and peak["time_hhmm"] == "00:21"


class TestMissingSectionIsEmptyNotAnError:
    """A report without the section is "no rows", never a crash. Before this
    fix ``parse_section_with_stats`` returned a bare list for a missing title
    and every caller that unpacked ``(rows, skipped)`` raised ValueError; a
    dry run has no Outfall Loading Summary at all."""

    def test_parse_section_on_a_report_without_the_section(self):
        from agentic_swmm.agent.swmm_runtime.rpt_summary import SECTIONS, parse_section, parse_section_with_stats

        assert parse_section("  Link Flow Summary\n", SECTIONS["Outfall Loading Summary"]) == []
        assert parse_section_with_stats("", SECTIONS["Node Flooding Summary"]) == ([], 0)

    def test_read_rpt_summary_reports_zero_rows(self, run_dir):
        from agentic_swmm.agent.tool_handlers.swmm_rpt import _read_rpt_summary_tool

        (run_dir / "06_runner" / "model.rpt").write_text("  Link Flow Summary\n", encoding="utf-8")
        call = ToolCall(
            "read_rpt_summary",
            {"rpt_path": str((run_dir / "06_runner" / "model.rpt").relative_to(REPO)),
             "section": "Outfall Loading Summary"},
        )
        payload = _read_rpt_summary_tool(call, REPO)
        assert payload["ok"] is True
        assert payload["rows"] == [] and payload["total_rows"] == 0
