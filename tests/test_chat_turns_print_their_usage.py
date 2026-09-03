"""A chat turn prints its LLM usage line like a run turn does (F-73).

Live finding 2026-09-02 (scenario S24): both chat-kind turns ended without
the "LLM usage" line, so the cost of a question was invisible while a
failed run turn showed it.
"""

from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from agentic_swmm.agent.planner import PlannerRun
from agentic_swmm.agent.tool_registry import AgentToolRegistry


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        planner="openai", provider="openai", model="gpt-test", session_id=None, session_dir=None,
        dry_run=False, interactive=False, max_steps=4, verbose=False, safe=False, quick=False, goal=[], example=None,
    )


def _chat_turn(session_dir: Path, *, seed_ledger: bool) -> str:
    from agentic_swmm.agent import runtime_loop

    session_dir.mkdir(parents=True, exist_ok=True)
    if seed_ledger:
        audit = session_dir / "09_audit"
        audit.mkdir(parents=True, exist_ok=True)
        rows = [
            {"caller": "planner", "tokens_input": 30_000, "tokens_output": 400},
            {"caller": "planner", "tokens_input": 12_000, "tokens_output": 200},
        ]
        (audit / "llm_calls.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf), mock.patch.object(
        runtime_loop, "run_openai_plan", return_value=PlannerRun(ok=True, plan=[], results=[], final_text="N4 flooded the most.")
    ), mock.patch.object(runtime_loop, "make_provider", return_value=mock.MagicMock()), mock.patch.object(
        runtime_loop, "ensure_session_pool"
    ), mock.patch.object(runtime_loop, "load_config", return_value=mock.MagicMock(get=lambda *_a, **_kw: "openai")):
        runtime_loop.run_openai_planner(
            _args(), goal="which node flooded the most?", session_dir=session_dir,
            trace_path=session_dir / "agent_trace.jsonl", registry=AgentToolRegistry(), chat_session=True,
        )
    return buf.getvalue()


def test_a_chat_turn_with_a_ledger_prints_the_usage_line(tmp_path):
    output = _chat_turn(tmp_path / "234619_run_chat", seed_ledger=True)
    assert "N4 flooded the most." in output
    assert "LLM usage: 2 call(s)" in output
    assert "42,000 in + 600 out" in output
    assert "Peak:" not in output, "a chat turn has no run block"


def test_a_chat_turn_without_a_ledger_prints_no_summary(tmp_path):
    output = _chat_turn(tmp_path / "234700_run_chat", seed_ledger=False)
    assert "LLM usage" not in output
