"""Wiring tests for the SWMMCanada -> run -> audit chain (W3, 2026-08).

The upstream fetch, the runner, and the audit verbs already exist; what
makes them a *chain* is (a) the natural-language route into the canada
intent, (b) the run_dir handoff landing every stage in one canonical
run folder, and (c) doctor knowing the upstream exists. These tests pin
all three. The live end-to-end (real service, real swmm5) lives in
``test_swmmcanada_live_smoke.py`` behind an env gate.
"""
from __future__ import annotations

import shutil
import urllib.error
import uuid
from pathlib import Path

import pytest

from agentic_swmm.agent.intent_classifier import (
    looks_like_swmm_request,
    select_relevant_intents,
)
from agentic_swmm.agent.swmm_runtime import run_layout
from agentic_swmm.agent.tool_handlers.swmm_runner import _run_swmm_inp_args
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.commands.doctor import _swmmcanada_upstream_check
from agentic_swmm.utils.paths import repo_root

EXAMPLE_INP = repo_root() / "examples" / "tecnopolo" / "tecnopolo_r1_199401.inp"


class TestRunDirHandoff:
    def test_fetched_inp_and_run_dir_land_in_the_same_run_folder(self, tmp_path):
        """The exact handoff the planner performs: take fetch_swmm_from_canada's
        returned run_dir + inp_path, call run_swmm_inp with both, and the
        simulation must land in that run folder's runner stage (ADR-0004),
        not in a fresh timestamped directory."""
        run_dir = repo_root() / "runs" / "agent" / f"canada-chain-test-{uuid.uuid4().hex[:8]}"
        try:
            builder = run_layout.stage_dir(run_dir, run_layout.BUILDER, create=True)
            inp = builder / "model.inp"
            shutil.copyfile(EXAMPLE_INP, inp)

            call = ToolCall(
                name="run_swmm_inp",
                args={"inp_path": str(inp), "run_dir": str(run_dir)},
            )
            mapped = _run_swmm_inp_args(call, tmp_path)

            assert mapped.get("ok") is not False, mapped
            assert mapped["inp"] == str(inp)
            expected_runner = run_layout.stage_dir(run_dir, run_layout.RUNNER)
            assert mapped["runDir"] == str(expected_runner)
            assert mapped.get("node")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class TestDefaultReportNode:
    """`aiswmm run` without --node must report a node that exists.

    The historical literal default O1 silently produced a null peak on
    every upstream model that names its outfalls differently (all
    SWMMCanada builds, observed live 2026-08-08)."""

    def test_first_outfall_wins(self, tmp_path):
        from agentic_swmm.agent.swmm_runtime.inp_parsing import default_report_node

        inp = tmp_path / "m.inp"
        inp.write_text(
            "[JUNCTIONS]\nJ7  1  1\n[OUTFALLS]\n;;comment\nOF_42  0  FREE\nOF_43  0  FREE\n",
            encoding="utf-8",
        )
        assert default_report_node(inp) == "OF_42"

    def test_junction_fallback(self, tmp_path):
        from agentic_swmm.agent.swmm_runtime.inp_parsing import default_report_node

        inp = tmp_path / "m.inp"
        inp.write_text("[JUNCTIONS]\nJ1  1  1\n", encoding="utf-8")
        assert default_report_node(inp) == "J1"

    def test_no_nodes_returns_none(self, tmp_path):
        from agentic_swmm.agent.swmm_runtime.inp_parsing import default_report_node

        inp = tmp_path / "m.inp"
        inp.write_text("[TITLE]\nempty\n", encoding="utf-8")
        assert default_report_node(inp) is None


class TestNaturalLanguageRoute:
    def test_canada_phrasing_counts_as_a_swmm_request(self):
        # Pre-W3 this returned False ("canada" was not a request keyword),
        # so skill priming skipped exactly the flagship upstream flow.
        assert looks_like_swmm_request("Get me a model for downtown Toronto, Canada")

    def test_city_phrasing_selects_the_fetch_canada_intent(self):
        intents = select_relevant_intents("Get me a model for downtown Toronto, Canada")
        assert "fetch-canada" in [intent.get("id") for intent in intents]

    def test_chinese_real_network_phrasing_selects_the_intent(self):
        intents = select_relevant_intents("给我温哥华的真实管网模型")
        assert "fetch-canada" in [intent.get("id") for intent in intents]


class TestDoctorUpstreamRow:
    def test_unset_env_is_a_quiet_ok(self, monkeypatch):
        monkeypatch.delenv("AISWMM_SWMMCANADA_URL", raising=False)
        name, ok, detail, required = _swmmcanada_upstream_check()
        assert name == "SWMMCanada upstream"
        assert ok is True
        assert "not configured" in detail
        assert required is False

    def test_unreachable_service_warns(self, monkeypatch):
        monkeypatch.setenv("AISWMM_SWMMCANADA_URL", "http://localhost:59999")

        def refuse(*args, **kwargs):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", refuse)
        name, ok, detail, required = _swmmcanada_upstream_check()
        assert ok is False
        assert "unreachable" in detail

    def test_healthy_service_reports_ok(self, monkeypatch):
        monkeypatch.setenv("AISWMM_SWMMCANADA_URL", "http://localhost:8000/")

        class _Resp:
            def read(self):
                return b'{"status": "ok", "version": "x"}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        seen = {}

        def fake_open(url, timeout=None):
            seen["url"] = url
            return _Resp()

        monkeypatch.setattr("urllib.request.urlopen", fake_open)
        name, ok, detail, required = _swmmcanada_upstream_check()
        assert ok is True
        assert "healthy" in detail
        assert seen["url"] == "http://localhost:8000/api/v1/healthz"
