"""Keyword-fallback priority regression for compound run+plot goals (#111).

Background
==========

The planner's auto-route fast-path mis-classified
``"run Tod Creek demo and plot the figure"`` as ``existing_run_plot``
because the keyword fallback's priority placed
``wants_plot AND has_run_dir`` before ``wants_demo`` / ``wants_run``.
The diagnosis trace (``runs/2026-05-16/120740_todcreek_run``) showed
the goal landed in keyword mode (no explicit ``mode`` from the LLM),
``wants_plot`` matched on "plot", an active run_dir was auto-inferred
from global state, and the wrong branch fired.

These tests lock the **keyword-level safety net** that runs when LLM
disambiguation falls back (timeout / offline / mock mode). The
LLM-disambiguator path is tested separately in
``tests/test_intent_disambiguator.py``.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_registry import _select_workflow_mode_tool
from agentic_swmm.agent.types import ToolCall


def _run(goal: str, tmp_path: Path, **extras: str) -> dict:
    args: dict[str, object] = {"goal": goal}
    args.update(extras)
    call = ToolCall(name="select_workflow_mode", args=args)
    return _select_workflow_mode_tool(call, tmp_path)


def test_run_demo_and_plot_routes_to_prepared_demo(tmp_path: Path) -> None:
    """The exact regression scenario. ``run`` + ``demo`` + ``plot`` all
    fire as keywords; before the fix, ``wants_plot`` won and the agent
    plotted an unrelated prior run. The fix adds a ``wants_run`` signal
    and a new branch that pins this combination to ``prepared_demo``
    so the user actually gets the demo they asked for."""

    payload = _run("run Tod Creek demo and plot the figure", tmp_path)
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_demo", payload


def test_bilingual_run_demo_and_plot_routes_to_prepared_demo(tmp_path: Path) -> None:
    """Bilingual variant — Chinese ``跑`` (run) + English ``demo`` +
    English ``plot``. The keyword fallback must handle CJK run verbs
    the same way it handles English ones (user story 8)."""

    payload = _run("跑 Tod Creek demo 然后 plot", tmp_path)
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_demo", payload


def test_chinese_run_verb_plus_demo_plus_plot(tmp_path: Path) -> None:
    """All-Chinese variant using the full ``运行`` form."""

    payload = _run("运行 Tod Creek 演示并画图", tmp_path)
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_demo", payload


def test_plot_only_with_active_run_dir_still_routes_to_plot(tmp_path: Path) -> None:
    """The fix must NOT regress the single-intent plot path. With only
    ``plot`` (no run/demo verb) and a ``run_dir`` provided, the keyword
    fallback still routes to ``existing_run_plot``."""

    payload = _run("plot the previous run", tmp_path, run_dir="/tmp/run")
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "existing_run_plot", payload


def test_calibration_only_unchanged(tmp_path: Path) -> None:
    """Single-intent calibration must keep routing to ``calibration``
    — the priority fix is narrowly scoped to plot-conflict goals."""

    payload = _run("calibrate this INP", tmp_path, inp_path="examples/foo.inp")
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "calibration", payload
