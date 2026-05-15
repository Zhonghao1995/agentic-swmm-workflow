"""Regression tests for the trimmed ``wants_plot`` keyword list.

PRD-INTENT-OVERMATCH (#95) Part 1: the original ``wants_plot`` tuple in
``_select_workflow_mode_tool`` included over-broad SWMM-domain vocabulary
(``rainfall``, ``node``, ``outfall``, single-char ``图``, node-attribute
names) that legitimately appears in non-plot prompts. A user-reported bug
on 2026-05-14 showed prompts like *"Run the SWMM input … audit rainfall
peak"* getting hijacked to ``existing_run_plot`` mode.

These tests lock the trimmed-keyword behaviour: SWMM-domain vocabulary in
the goal must NOT trigger plot mode by itself, and real plot verbs (both
English and Chinese) must still work.
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


def test_rainfall_in_goal_no_longer_hijacks_to_plot(tmp_path: Path) -> None:
    """The exact user-reported scenario: a goal mentioning ``rainfall``
    plus an ``inp_path`` and no explicit ``mode`` must route to
    ``prepared_inp_cli`` — the model needs to actually run SWMM."""

    payload = _run(
        "Run the SWMM input file at examples/tecnopolo_r1_199401.inp and "
        "audit the rainfall peak",
        tmp_path,
        inp_path="examples/tecnopolo_r1_199401.inp",
    )
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_inp_cli", payload


def test_outfall_in_goal_no_longer_hijacks_to_plot(tmp_path: Path) -> None:
    """Same anti-pattern with the word ``outfall``. SWMM-domain entities
    appearing in the goal must not force plot mode by themselves."""

    payload = _run(
        "Run the model and look at outfall flow",
        tmp_path,
        inp_path="examples/foo.inp",
    )
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_inp_cli", payload


def test_chinese_plot_verb_still_triggers_plot_mode(tmp_path: Path) -> None:
    """Real Chinese plot verbs (``画图`` and ``作图``) must continue to
    route to ``existing_run_plot`` mode when a ``run_dir`` is supplied.
    The trim removes only the over-broad single-char ``图``."""

    payload = _run("画图看看 outfall 的流量", tmp_path, run_dir="/tmp/run")
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "existing_run_plot", payload

    payload = _run("作图分析", tmp_path, run_dir="/tmp/run")
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "existing_run_plot", payload


def test_chinese_single_char_图_alone_does_not_trigger_plot(
    tmp_path: Path,
) -> None:
    """The single Chinese character ``图`` appears in many non-plot words
    (``地图`` = map, ``示意图`` = diagram, ``图层`` = layer). It must no
    longer match the plot-intent guard by itself. With ``inp_path``
    provided the prompt should route to ``prepared_inp_cli``."""

    payload = _run(
        "根据地图运行 SWMM 模型",
        tmp_path,
        inp_path="examples/foo.inp",
    )
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "prepared_inp_cli", payload
