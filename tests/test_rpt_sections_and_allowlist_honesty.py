"""Typed answers for the questions a client asks after a run, and an honest
``run_allowed_command``.

Live findings F-04 and F-10 (2026-09-02, pty-driven session S02): the
follow-up "Which node flooded the most, and for how long?" had no typed
answer because ``read_rpt_summary`` did not know the Node Flooding Summary,
so the planner tried five variants of ``python -c`` through
``run_allowed_command`` (each refused with a bare "not allowlisted", one
passing the check and then raising FileNotFoundError because this machine
has no ``python``), was asked "Continue past these failures?", patched a
helper script into the repo, deleted it again, and stopped at max_steps with
1.19M input tokens spent.

Pinned here:

* five more rpt sections parse into typed rows with a decision-relevant
  default sort (Node Flooding, Node Surcharge, Node Depth, Conduit
  Surcharge, Subcatchment Runoff);
* a header that merely contains dashes (``--------- Hours Full --------``)
  is not a table rule, and a section that printed no table ("No nodes were
  flooded.") yields nothing instead of the next section's rows;
* ``run_allowed_command`` accepts ``python3``, runs python through the
  interpreter that runs aiswmm, and says what IS allowed when it refuses.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest

from agentic_swmm.agent.swmm_runtime.rpt_summary import SECTIONS, parse_section, parse_section_with_stats
from agentic_swmm.agent.tool_handlers.swmm_rpt import _read_rpt_summary_tool
from agentic_swmm.agent.tool_registry import (
    AgentToolRegistry,
    _command_allowed,
    _run_allowed_command_tool,
)
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.utils.paths import repo_root

# Rows lifted from a real SWMM 5.2.4 rpt (James Bay, Victoria BC, 2026-09-02).
RPT = """\
  EPA STORM WATER MANAGEMENT MODEL - VERSION 5.2 (Build 5.2.4)
  ------------------------------------------------------------

  ***************************
  Subcatchment Runoff Summary
  ***************************

  ------------------------------------------------------------------------------------------------------------------------------
                            Total      Total      Total      Total     Imperv       Perv      Total       Total     Peak  Runoff
                           Precip      Runon       Evap      Infil     Runoff     Runoff     Runoff      Runoff   Runoff   Coeff
  Subcatchment                 mm         mm         mm         mm         mm         mm         mm    10^6 ltr      CMS
  ------------------------------------------------------------------------------------------------------------------------------
  S_DMH003533               41.80       0.00       1.51       8.38      27.42       2.85      30.27        0.21     0.01   0.724
  S_DMH005419               41.80       0.00       1.55      13.07      20.81       4.37      25.19        0.66     0.03   0.603


  ******************
  Node Depth Summary
  ******************

  ---------------------------------------------------------------------------------
                                 Average  Maximum  Maximum  Time of Max    Reported
                                   Depth    Depth      HGL   Occurrence   Max Depth
  Node                 Type       Meters   Meters   Meters  days hr:min      Meters
  ---------------------------------------------------------------------------------
  DMH003533            JUNCTION     0.04     0.13     9.40     1  05:00        0.13
  DMH005281            JUNCTION     0.71     1.45     0.86     0  14:00        1.45


  **********************
  Node Surcharge Summary
  **********************

  Surcharging occurs when water rises above the top of the highest conduit.
  ---------------------------------------------------------------------
                                               Max. Height   Min. Depth
                                   Hours       Above Crown    Below Rim
  Node                 Type      Surcharged         Meters       Meters
  ---------------------------------------------------------------------
  DMH005419            JUNCTION        3.00          0.920        0.000
  DMH001492            JUNCTION       14.91          1.114        0.206


  *********************
  Node Flooding Summary
  *********************

  Flooding refers to all water that overflows a node, whether it ponds or not.
  --------------------------------------------------------------------------
                                                             Total   Maximum
                                 Maximum   Time of Max       Flood    Ponded
                        Hours       Rate    Occurrence      Volume     Depth
  Node                 Flooded       CMS   days hr:min    10^6 ltr    Meters
  --------------------------------------------------------------------------
  DMH005419               1.35     0.010      1  05:00       0.032     0.000
  DMH001395               9.16     0.155      1  05:00       2.129     0.000
  DMH001486               1.95     0.117      1  05:00       0.479     0.000


  ***********************
  Outfall Loading Summary
  ***********************

  -----------------------------------------------------------
                         Flow       Avg       Max       Total
                         Freq      Flow      Flow      Volume
  Outfall Node           Pcnt       CMS       CMS    10^6 ltr
  -----------------------------------------------------------
  DOF007052              0.00     0.000     0.000       0.000
  OUT_DMH002395         73.68     0.046     0.116       2.745
  -----------------------------------------------------------
  System                36.84     0.046     0.116       2.745


  *************************
  Conduit Surcharge Summary
  *************************

  ----------------------------------------------------------------------------
                                                           Hours        Hours
                         --------- Hours Full --------   Above Full   Capacity
  Conduit                Both Ends  Upstream  Dnstream   Normal Flow   Limited
  ----------------------------------------------------------------------------
  DGM005281                  27.57     27.57     42.02      0.01         0.01
  DGM005419                   0.01      3.00      0.01      2.27         0.01

"""

# A dry run: SWMM prints a sentence instead of a table.
DRY_RPT = """\
  *********************
  Node Flooding Summary
  *********************

  No nodes were flooded.

  ***********************
  Outfall Loading Summary
  ***********************

  -----------------------------------------------------------
                         Flow       Avg       Max       Total
                         Freq      Flow      Flow      Volume
  Outfall Node           Pcnt       CMS       CMS    10^6 ltr
  -----------------------------------------------------------
  OUT_1                 10.00     0.001     0.002       0.010
  -----------------------------------------------------------
  System                10.00     0.001     0.002       0.010

"""


@pytest.fixture(scope="module")
def rpt_dir():
    # ``_required_repo_file`` only accepts files under the repo root.
    scratch = repo_root() / "runs" / "_test_rpt_sections"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    (scratch / "model.rpt").write_text(RPT, encoding="utf-8")
    (scratch / "dry.rpt").write_text(DRY_RPT, encoding="utf-8")
    yield scratch
    shutil.rmtree(scratch, ignore_errors=True)


class TestNewSectionsParse:
    def test_node_flooding_rows_are_typed(self):
        rows = parse_section(RPT, SECTIONS["Node Flooding Summary"])
        assert [row["node"] for row in rows] == ["DMH005419", "DMH001395", "DMH001486"]
        worst = rows[1]
        assert worst["hours_flooded"] == 9.16
        assert worst["max_flood_rate"] == 0.155
        assert worst["time_days"] == 1 and worst["time_hhmm"] == "05:00"
        assert worst["total_flood_volume_10_6_ltr"] == 2.129
        assert worst["max_ponded_depth"] == 0.0

    def test_node_surcharge_rows_are_typed(self):
        rows = parse_section(RPT, SECTIONS["Node Surcharge Summary"])
        assert [row["node"] for row in rows] == ["DMH005419", "DMH001492"]
        assert rows[1]["hours_surcharged"] == 14.91
        assert rows[1]["max_height_above_crown"] == 1.114
        assert rows[1]["min_depth_below_rim"] == 0.206

    def test_node_depth_rows_are_typed(self):
        rows = parse_section(RPT, SECTIONS["Node Depth Summary"])
        assert rows[1]["node"] == "DMH005281"
        assert rows[1]["max_depth"] == 1.45
        assert rows[1]["max_hgl"] == 0.86
        assert rows[1]["time_hhmm"] == "14:00"
        assert rows[1]["reported_max_depth"] == 1.45

    def test_conduit_surcharge_header_dashes_are_not_a_rule(self):
        rows, skipped = parse_section_with_stats(RPT, SECTIONS["Conduit Surcharge Summary"])
        assert [row["conduit"] for row in rows] == ["DGM005281", "DGM005419"]
        assert skipped == 0
        assert rows[0]["hours_full_both_ends"] == 27.57
        assert rows[0]["hours_full_downstream"] == 42.02
        assert rows[1]["hours_above_full_normal_flow"] == 2.27

    def test_subcatchment_runoff_rows_are_typed(self):
        rows = parse_section(RPT, SECTIONS["Subcatchment Runoff Summary"])
        assert [row["subcatchment"] for row in rows] == ["S_DMH003533", "S_DMH005419"]
        assert rows[1]["total_infil"] == 13.07
        assert rows[1]["total_runoff_volume_10_6_ltr"] == 0.66
        assert rows[1]["peak_runoff"] == 0.03
        assert rows[1]["runoff_coeff"] == 0.603

    def test_a_section_without_a_table_yields_nothing(self):
        rows, skipped = parse_section_with_stats(DRY_RPT, SECTIONS["Node Flooding Summary"])
        assert rows == [] and skipped == 0
        # And the neighbouring section still parses on its own.
        outfalls = parse_section(DRY_RPT, SECTIONS["Outfall Loading Summary"])
        assert [row["node"] for row in outfalls] == ["OUT_1"]

    def test_existing_sections_are_untouched(self):
        outfalls = parse_section(RPT, SECTIONS["Outfall Loading Summary"])
        assert [row["node"] for row in outfalls] == ["DOF007052", "OUT_DMH002395"]
        assert outfalls[1]["total_volume_10_6_ltr"] == 2.745


class TestToolAnswersTheClientQuestion:
    def test_which_node_flooded_the_most_is_one_call(self, rpt_dir):
        call = ToolCall(
            "read_rpt_summary",
            {"rpt_path": str((rpt_dir / "model.rpt").relative_to(repo_root())),
             "section": "Node Flooding Summary", "top_n": 1},
        )
        payload = _read_rpt_summary_tool(call, repo_root())
        assert payload["ok"] is True
        assert payload["sort_by"] == "total_flood_volume_10_6_ltr"
        assert payload["rows"][0]["node"] == "DMH001395"
        assert payload["rows"][0]["hours_flooded"] == 9.16
        assert payload["total_rows"] == 3

    def test_for_how_long_can_sort_by_hours(self, rpt_dir):
        call = ToolCall(
            "read_rpt_summary",
            {"rpt_path": str((rpt_dir / "model.rpt").relative_to(repo_root())),
             "section": "Node Surcharge Summary", "sort_by": "hours_surcharged", "top_n": 1},
        )
        payload = _read_rpt_summary_tool(call, repo_root())
        assert payload["rows"][0]["node"] == "DMH001492"

    def test_a_dry_run_says_so_instead_of_borrowing_rows(self, rpt_dir):
        call = ToolCall(
            "read_rpt_summary",
            {"rpt_path": str((rpt_dir / "dry.rpt").relative_to(repo_root())),
             "section": "Node Flooding Summary"},
        )
        payload = _read_rpt_summary_tool(call, repo_root())
        assert payload["ok"] is True
        assert payload["rows"] == []
        assert payload["total_rows"] == 0
        assert "skipped_malformed_rows" not in payload

    def test_the_planner_can_see_the_new_sections(self):
        registry = AgentToolRegistry()
        spec = next(s for s in registry.schemas() if s["name"] == "read_rpt_summary")
        enum = spec["parameters"]["properties"]["section"]["enum"]
        for section in (
            "Node Flooding Summary", "Node Surcharge Summary", "Node Depth Summary",
            "Conduit Surcharge Summary", "Subcatchment Runoff Summary",
        ):
            assert section in enum
        description = (registry.describe("read_rpt_summary") or "").lower()
        assert "which node flooded the most" in description


class TestRunAllowedCommandHonesty:
    @pytest.mark.parametrize(
        "command",
        [
            ["python3", "-m", "agentic_swmm.cli", "doctor"],
            ["python3.11", "-m", "pytest", "tests/"],
        ],
    )
    def test_python3_spellings_are_allowed(self, command):
        assert _command_allowed(command) is True

    @pytest.mark.parametrize(
        "command",
        [
            ["python3", "-c", "print(1)"],
            ["python", "-c", "import json"],
            ["grep", "-n", "Flooding", "model.rpt"],
        ],
    )
    def test_ad_hoc_code_is_still_refused(self, command):
        assert _command_allowed(command) is False

    def test_a_refusal_says_what_is_allowed_and_where_to_look_instead(self, tmp_path):
        call = ToolCall("run_allowed_command", {"command": ["python", "-c", "print(1)"]})
        payload = _run_allowed_command_tool(call, tmp_path)
        assert payload["ok"] is False
        assert "allowed:" in payload["summary"]
        assert "python -m agentic_swmm.cli" in payload["summary"]
        assert "read_rpt_summary" in payload["hint"]
        assert "not a shell" in payload["hint"]

    def test_python_runs_through_the_interpreter_that_runs_aiswmm(self, tmp_path):
        call = ToolCall("run_allowed_command", {"command": ["python", "-m", "agentic_swmm.cli", "--help"]})
        with mock.patch("agentic_swmm.agent.tool_registry._run_process_tool", return_value={"ok": True}) as run:
            _run_allowed_command_tool(call, tmp_path)
        spawned = run.call_args.args[2]
        assert spawned[0] == sys.executable
        assert spawned[1:] == ["-m", "agentic_swmm.cli", "--help"]

    def test_the_description_tells_the_planner_before_it_tries(self):
        description = AgentToolRegistry().describe("run_allowed_command") or ""
        assert "python -m agentic_swmm.cli" in description
        assert "python -c" in description
        assert "read_rpt_summary" in description
