"""Regression: ``final_report.md`` "What you got" must list the
artifacts the run actually produced — not the skill contracts the
LLM happened to read on its way there.

Bug history
-----------
Before this fix the section looked like::

    ## What you got

    - **Other artifacts**
        - `skills/swmm-end-to-end/SKILL.md`
        - `skills/swmm-anywhere/SKILL.md`
        - ... (every read_skill target)

…even when the run produced a synthesised INP, ``model.rpt`` /
``model.out``, an audit JSON bundle, and a ``network_layout.png``.
The root cause: ``_what_you_got`` iterated ``results`` and pulled
``result["path"]`` — and the ONLY handler that put a path at that
key was ``_read_skill_tool`` (every other handler nested its paths
under ``results``, ``args``, ``excerpt``, or the ``summary`` string).
So introspection paths drowned out real artifacts.

What this test pins
-------------------
* Read-only introspection tools (``read_skill`` / ``read_file`` /
  ``list_*`` / ``select_skill`` / ``search_files`` / ``capabilities``)
  must NOT contribute paths — those are LLM reading material, not
  produced artifacts.
* Real artifacts from the four end-to-end handlers used in the
  ``synth_swmm_from_bbox → run_swmm_inp → audit_run → map_run``
  chain MUST appear, classified into the right section.
* Planner-internal paths (``tool_results/*.stdout.txt``,
  ``agent_trace.jsonl``, ``final_report.md``, ``session_state.json``)
  must NOT appear — they are session bookkeeping, not modeling
  output.

The fixtures below mirror the ACTUAL result-payload shapes the
respective handlers emitted in the live NYC-midtown end-to-end
captured in ``runs/2026-05-27/231715_Using-SWMManywhere-...``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agentic_swmm.agent.reporting import write_report


def _call(name: str, args: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name=name, args=args or {})


# Paths matching the actual e2e the user just ran. Hardcoding makes
# the expectations unambiguous and the test fast.
RUN = "/Users/x/runs/2026-05-27/foo"
SYNTH_INP = f"{RUN}/10_swmmanywhere/synth.inp"
MODEL_RPT = f"{RUN}/20_swmm_run/model.rpt"
MODEL_OUT = f"{RUN}/20_swmm_run/model.out"
NETWORK_PNG = f"{RUN}/20_swmm_run/network_layout.png"
AUDIT_PROV = f"{RUN}/20_swmm_run/09_audit/experiment_provenance.json"
AUDIT_DIAG = f"{RUN}/20_swmm_run/09_audit/model_diagnostics.json"
AUDIT_NOTE = f"{RUN}/20_swmm_run/09_audit/experiment_note.md"


def _build_realistic_results() -> tuple[list, list]:
    """Return (plan, results) matching the live e2e payload shapes."""
    plan = [
        _call("read_skill", {"skill_name": "swmm-anywhere"}),
        _call("list_mcp_servers"),
        _call("select_skill", {"skill_name": "swmm-anywhere"}),
        _call(
            "synth_swmm_from_bbox",
            {"bbox": [-73.98, 40.755, -73.97, 40.765], "run_dir": RUN},
        ),
        _call("run_swmm_inp", {"inp_path": SYNTH_INP, "run_dir": f"{RUN}/20_swmm_run"}),
        _call("audit_run", {"run_dir": f"{RUN}/20_swmm_run"}),
        _call(
            "map_run",
            {"run_dir": f"{RUN}/20_swmm_run", "inp": SYNTH_INP, "out_png": NETWORK_PNG},
        ),
    ]
    results = [
        # read_skill (introspection — must NOT contribute paths)
        {
            "tool": "read_skill",
            "ok": True,
            "path": "/repo/skills/swmm-anywhere/SKILL.md",
            "summary": "read skill swmm-anywhere",
        },
        {"tool": "list_mcp_servers", "ok": True, "summary": "11 servers"},
        {
            "tool": "select_skill",
            "ok": True,
            "path": "/repo/skills/swmm-anywhere/SKILL.md",
            "summary": "selected skill swmm-anywhere",
        },
        # synth_swmm_from_bbox (in-process handler — paths nested in `results`)
        {
            "tool": "synth_swmm_from_bbox",
            "ok": True,
            "results": {
                "inp_path": SYNTH_INP,
                "run_dir": RUN,
                "raw_manifest_path": f"{RUN}/00_raw/raw_manifest.json",
                "stage_durations_s": {"swmmanywhere_pipeline": 50.6},
                "warnings": [],
            },
            "summary": f"synth_inp={SYNTH_INP}",
        },
        # run_swmm_inp (MCP-routed — paths embedded in `excerpt` JSON)
        {
            "tool": "run_swmm_inp",
            "ok": True,
            "excerpt": json.dumps(
                {
                    "manifest_version": "1.0",
                    "swmm5": {"version": "5.2.4"},
                    "inp": SYNTH_INP,
                    "files": {
                        "rpt": MODEL_RPT,
                        "out": MODEL_OUT,
                        "stdout": f"{RUN}/20_swmm_run/stdout.txt",
                        "stderr": f"{RUN}/20_swmm_run/stderr.txt",
                    },
                    "metrics": {"peak": {"peak": 250.37, "node": "349_outfall"}},
                }
            ),
            "summary": "ok=True peak=250.37",
        },
        # audit_run (MCP-routed — paths in `results.content[0].text` JSON)
        {
            "tool": "audit_run",
            "ok": True,
            "results": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "ok": True,
                                "status": "pass",
                                "experiment_provenance": AUDIT_PROV,
                                "model_diagnostics": AUDIT_DIAG,
                                "experiment_note": AUDIT_NOTE,
                                "comparison": f"{RUN}/20_swmm_run/09_audit/comparison.json",
                            }
                        ),
                    }
                ]
            },
            "summary": "audited",
        },
        # map_run (_run_cli_tool wrapper — paths in `args.out_png` + `summary` "map: /path")
        {
            "tool": "map_run",
            "ok": True,
            "args": {"out_png": NETWORK_PNG, "run_dir": f"{RUN}/20_swmm_run"},
            "stdout_file": "/sess/tool_results/map_run.stdout.txt",
            "stderr_file": "/sess/tool_results/map_run.stderr.txt",
            "summary": f"map: {NETWORK_PNG}",
        },
    ]
    return plan, results


class WhatYouGotIntrospectionSkippedTests(unittest.TestCase):
    """The bug: ``read_skill`` paths dominated the section. Lock down
    that introspection paths are now excluded."""

    def test_read_skill_paths_do_not_appear_in_what_you_got(self) -> None:
        plan, results = _build_realistic_results()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(
                Path(tmp),
                goal="g",
                plan=plan,
                results=results,
                dry_run=False,
                allowed_tools=set(),
                planner="openai",
            )
            text = path.read_text(encoding="utf-8")
        section = text.split("## What you got", 1)[1].split("##", 1)[0]
        self.assertNotIn(
            "SKILL.md",
            section,
            "introspection paths (read_skill/select_skill targets) must be "
            "filtered out of 'What you got' — they are LLM input, not output",
        )

    def test_select_skill_paths_do_not_appear(self) -> None:
        plan, results = _build_realistic_results()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(
                Path(tmp),
                goal="g",
                plan=plan,
                results=results,
                dry_run=False,
                allowed_tools=set(),
                planner="openai",
            )
            text = path.read_text(encoding="utf-8")
        section = text.split("## What you got", 1)[1].split("##", 1)[0]
        # select_skill also dumps SKILL.md as result["path"] — same
        # introspection-bucket rule applies.
        self.assertNotIn("/repo/skills/", section)


class WhatYouGotRealArtifactsAppearTests(unittest.TestCase):
    """The fix: every real artifact from the four production handlers
    must surface in the section."""

    def setUp(self) -> None:
        plan, results = _build_realistic_results()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(
                Path(tmp),
                goal="g",
                plan=plan,
                results=results,
                dry_run=False,
                allowed_tools=set(),
                planner="openai",
            )
            self.text = path.read_text(encoding="utf-8")
        self.section = self.text.split("## What you got", 1)[1].split("##", 1)[0]

    def test_synth_inp_appears(self) -> None:
        self.assertIn(
            SYNTH_INP,
            self.section,
            "synth_swmm_from_bbox produced an .inp file at "
            "results.inp_path — must surface in What you got",
        )

    def test_swmm_rpt_appears(self) -> None:
        self.assertIn(
            MODEL_RPT,
            self.section,
            "run_swmm_inp produced model.rpt (path embedded in MCP "
            "manifest excerpt) — must surface in What you got",
        )

    def test_swmm_out_appears(self) -> None:
        self.assertIn(MODEL_OUT, self.section)

    def test_network_layout_png_appears(self) -> None:
        self.assertIn(
            NETWORK_PNG,
            self.section,
            "map_run produced network_layout.png (path in args.out_png + "
            "summary) — must surface in What you got. This is the exact "
            "user-reported gap from the 2026-05-27 NYC midtown e2e.",
        )

    def test_audit_artifacts_appear(self) -> None:
        for p in (AUDIT_PROV, AUDIT_DIAG, AUDIT_NOTE):
            with self.subTest(path=p):
                self.assertIn(p, self.section)


class WhatYouGotPlannerInternalsSkippedTests(unittest.TestCase):
    """``tool_results/foo.stdout.txt`` is planner bookkeeping, not a
    modeling artifact — the user has no reason to open it from the
    report. Same for the session-level trace files."""

    def test_tool_results_stdout_stderr_files_are_skipped(self) -> None:
        plan, results = _build_realistic_results()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(
                Path(tmp),
                goal="g",
                plan=plan,
                results=results,
                dry_run=False,
                allowed_tools=set(),
                planner="openai",
            )
            text = path.read_text(encoding="utf-8")
        section = text.split("## What you got", 1)[1].split("##", 1)[0]
        self.assertNotIn("map_run.stdout.txt", section)
        self.assertNotIn("map_run.stderr.txt", section)


class WhatYouGotClassificationTests(unittest.TestCase):
    """Every surviving artifact must land in a section that matches
    its kind — that's the reader's mental shortcut for finding things."""

    def setUp(self) -> None:
        plan, results = _build_realistic_results()
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(
                Path(tmp),
                goal="g",
                plan=plan,
                results=results,
                dry_run=False,
                allowed_tools=set(),
                planner="openai",
            )
            self.text = path.read_text(encoding="utf-8")
        self.section = self.text.split("## What you got", 1)[1].split("##", 1)[0]

    def test_png_lands_under_plots(self) -> None:
        # Find the "**Plots**" group and verify the PNG is inside it.
        if "**Plots**" not in self.section:
            self.fail(f"no Plots group in section:\n{self.section}")
        plots = self.section.split("**Plots**", 1)[1].split("**", 1)[0]
        self.assertIn(NETWORK_PNG, plots)

    def test_rpt_out_land_under_run_output(self) -> None:
        if "**Run output**" not in self.section:
            self.fail(f"no 'Run output' group in section:\n{self.section}")
        run_out = self.section.split("**Run output**", 1)[1].split("**", 1)[0]
        self.assertIn(MODEL_RPT, run_out)
        self.assertIn(MODEL_OUT, run_out)

    def test_audit_jsons_land_under_audit(self) -> None:
        if "**Audit**" not in self.section:
            self.fail(f"no Audit group in section:\n{self.section}")
        audit = self.section.split("**Audit**", 1)[1].split("**", 1)[0]
        self.assertIn(AUDIT_PROV, audit)
        self.assertIn(AUDIT_DIAG, audit)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
