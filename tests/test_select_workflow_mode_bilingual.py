"""Regression tests for bilingual goal routing in ``select_workflow_mode``.

P0-2 in the 2026-05-14 architecture review (#79): the Chinese keywords in
``_select_workflow_mode_tool`` had been silently replaced by literal ``"??"``
placeholders by a non-UTF-8 sync. That made Chinese-only goal routing dead
*and* made any prompt containing ``??`` match calibration, uncertainty,
audit, and demo simultaneously. These tests lock both halves of the fix:

1. Chinese prompts route to the expected mode.
2. The literal placeholder pairs cannot reappear in the routing source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agentic_swmm.agent.tool_registry import _select_workflow_mode_tool
from agentic_swmm.agent.types import ToolCall


def _run(goal: str, tmp_path: Path, **extras: str) -> dict:
    args: dict[str, object] = {"goal": goal}
    args.update(extras)
    call = ToolCall(name="select_workflow_mode", args=args)
    return _select_workflow_mode_tool(call, tmp_path)


@pytest.mark.parametrize(
    "goal,expected_mode",
    [
        ("跑校准看看 NSE", "calibration"),
        ("做率定", "calibration"),
        ("做不确定性分析", "uncertainty"),
        ("敏感性分析", "uncertainty"),
        ("演示一下", "prepared_demo"),
        ("验收测试", "prepared_demo"),
    ],
)
def test_chinese_goal_routes_to_expected_mode(
    goal: str, expected_mode: str, tmp_path: Path
) -> None:
    payload = _run(goal, tmp_path)
    assert payload["ok"] is True
    assert payload["results"]["mode"] == expected_mode, payload


def test_chinese_compare_routes_to_audit_with_baseline_required(tmp_path: Path) -> None:
    payload = _run("比较两个 run_dir", tmp_path, run_dir="/tmp/run")
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "audit_only_or_comparison"
    assert "baseline_run_dir" in payload["results"]["required_inputs"]


def test_chinese_audit_routes_without_inp(tmp_path: Path) -> None:
    payload = _run("做审计", tmp_path, run_dir="/tmp/run")
    assert payload["ok"] is True
    assert payload["results"]["mode"] == "audit_only_or_comparison"


def test_bare_question_marks_do_not_force_demo_or_calibration(tmp_path: Path) -> None:
    """A prompt containing only ``??`` (e.g. a user pasting question marks)
    must NOT match calibration / uncertainty / demo / audit. This was the
    silent-regression failure mode in P0-2."""

    payload = _run("??", tmp_path)
    assert payload["ok"] is True
    # With no real keywords and no inputs, fall through to needs_user_inputs.
    assert payload["results"]["mode"] == "needs_user_inputs", payload


def test_no_placeholder_question_marks_in_routing_source() -> None:
    """File-grep regression lock: the literal ``"??"`` / ``"???"`` ASCII
    placeholders must never reappear in the routing logic. If a future
    sync clobbers UTF-8 again, this test trips first.

    Note (#111): the CJK keyword tuples used to live inline inside
    ``_select_workflow_mode_tool``. They were extracted to
    ``compute_intent_signals`` so the planner's auto-route
    disambiguator and the keyword fallback share one source of truth.
    The placeholder / CJK regression checks now run against
    ``compute_intent_signals`` since that is the function that owns the
    keyword tuples."""

    src = (
        Path(__file__).resolve().parents[1]
        / "agentic_swmm"
        / "agent"
        / "tool_registry.py"
    )
    text = src.read_text(encoding="utf-8")
    start = text.index("def compute_intent_signals(")
    end = text.index("def ", start + 1)
    body = text[start:end]
    assert '"??"' not in body, (
        "Found literal '\"??\"' placeholder in compute_intent_signals body — "
        "Chinese keywords have been re-clobbered (see #79 P0-2)."
    )
    assert '"???"' not in body, (
        "Found literal '\"???\"' placeholder in compute_intent_signals body — "
        "Chinese keywords have been re-clobbered (see #79 P0-2)."
    )
    # Sanity: every routing branch should mention at least one CJK keyword.
    cjk = re.compile(r"[一-鿿]")
    assert cjk.search(body), "No Chinese keyword found in routing body"
