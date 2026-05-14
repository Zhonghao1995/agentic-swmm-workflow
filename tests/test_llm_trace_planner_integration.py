"""Integration: every ``OpenAIPlanner`` LLM call lands in the trace.

PRD-LLM-TRACE wires ``record_llm_call`` into
``agent/planner.py`` around the ``provider.respond_with_tools`` call.
A two-round planner cycle (one LLM tool-call round + one final text
round) must therefore produce exactly two entries in
``09_audit/llm_calls.jsonl`` with ``caller="planner"`` and
``model_role="decide_next_tool"``, and two matching prompt dumps.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


NON_SWMM_GOAL = "tell me about this repository"


class _ScriptedProvider:
    """Scripted provider that also exposes a fake ``usage`` block on
    each response so the observer can pull token counts."""

    def __init__(self, responses: list[ProviderToolResponse]) -> None:
        self._responses = list(responses)
        self.calls_received: list[list[dict[str, Any]]] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self.calls_received.append(list(input_items))
        if not self._responses:
            raise AssertionError(
                "scripted provider exhausted; planner asked for more responses than the test scripted"
            )
        return self._responses.pop(0)


class _ScriptedExecutor:
    def __init__(self, tool_results: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self._tool_results = tool_results or {}
        self.results: list[dict[str, Any]] = []
        self.dry_run = False
        self.recorded: list[ToolCall] = []

    def execute(self, call: ToolCall, *, index: int) -> dict[str, Any]:
        self.recorded.append(call)
        queue = self._tool_results.get(call.name)
        if queue:
            result = dict(queue.pop(0))
            result.setdefault("tool", call.name)
            result.setdefault("args", call.args)
        else:
            result = {"tool": call.name, "args": call.args, "ok": True, "summary": "ok"}
        self.results.append(result)
        return result


def _tool_call(name: str, args: dict[str, Any] | None = None, *, call_id: str | None = None) -> ProviderToolCall:
    return ProviderToolCall(call_id=call_id or f"call_{name}", name=name, arguments=args or {})


def _tool_response(calls: list[ProviderToolCall], *, response_id: str = "stub-step") -> ProviderToolResponse:
    return ProviderToolResponse(
        text="",
        model="claude-opus-4-7",
        response_id=response_id,
        tool_calls=calls,
        raw={"usage": {"input_tokens": 1000, "output_tokens": 200}},
    )


def _final(text: str) -> ProviderToolResponse:
    return ProviderToolResponse(
        text=text,
        model="claude-opus-4-7",
        response_id="stub-final",
        tool_calls=[],
        raw={"usage": {"input_tokens": 1100, "output_tokens": 50}},
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


class PlannerIntegrationTests(unittest.TestCase):
    def test_each_provider_call_records_an_llm_trace_entry(self) -> None:
        # Two-round planner: one tool call, then a final-text round.
        provider = _ScriptedProvider(
            [
                _tool_response(
                    [_tool_call("read_file", {"path": "x.txt"}, call_id="c1")],
                    response_id="r1",
                ),
                _final("done"),
            ]
        )
        executor = _ScriptedExecutor(
            {
                "read_file": [{"ok": True, "summary": "read"}],
            }
        )

        with tempfile.TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            trace_path = session_dir / "agent_trace.jsonl"
            planner = OpenAIPlanner(
                provider=provider,  # type: ignore[arg-type]
                registry=AgentToolRegistry(),
                max_steps=4,
                verbose=False,
                emit=lambda text: None,
            )
            outcome = planner.run(
                goal=NON_SWMM_GOAL,
                session_dir=session_dir,
                trace_path=trace_path,
                executor=executor,
            )
            self.assertTrue(outcome.ok)

            llm_calls_path = session_dir / "09_audit" / "llm_calls.jsonl"
            self.assertTrue(
                llm_calls_path.is_file(),
                "planner must record LLM calls under 09_audit/llm_calls.jsonl",
            )
            entries = _read_jsonl(llm_calls_path)
            self.assertEqual(
                len(entries),
                2,
                f"expected 2 LLM call entries (one per respond_with_tools call), got {len(entries)}",
            )

            for entry in entries:
                self.assertEqual(entry["caller"], "planner")
                self.assertEqual(entry["model_role"], "decide_next_tool")
                self.assertEqual(entry["model_alias"], "claude-opus-4-7")
                # Token counts from the mocked usage block.
                self.assertIsInstance(entry["tokens_input"], int)
                self.assertIsInstance(entry["tokens_output"], int)
                # Duration was measured at the call site.
                self.assertIsInstance(entry["duration_ms"], int)
                # Prompt dump exists on disk.
                dump_path = session_dir / entry["prompt_full_ref"]
                self.assertTrue(
                    dump_path.is_file(),
                    f"prompt dump missing: {dump_path}",
                )
                # System prompt content makes it into the dump (sanity).
                self.assertGreater(len(dump_path.read_text(encoding="utf-8")), 0)

            # First entry should reflect the tool-call response.
            self.assertEqual(entries[0]["tool_calls_emitted"], ["read_file"])
            # Second entry: final text, no tool calls.
            self.assertEqual(entries[1]["tool_calls_emitted"], [])
            self.assertIn("done", entries[1]["response_text"])


if __name__ == "__main__":
    unittest.main()
