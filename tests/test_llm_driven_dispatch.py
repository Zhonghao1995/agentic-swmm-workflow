"""Integration tests for the LLM-driven dispatch refactor.

Before this refactor, the planner forced a ``select_workflow_mode`` first
hop and dispatched into a ``workflow_modes/<mode>.py`` adapter; the LLM
never saw the concrete SWMM tools directly. Post-refactor the planner
sends the full ``AgentToolRegistry.schemas()`` to the provider and the
LLM picks tools by name.

These tests pin the LLM-driven dispatch contract by feeding a scripted
provider into the real :class:`OpenAIPlanner` and asserting the tool
calls the LLM emits actually reach the executor — *no* keyword
re-classification, *no* mode gate, *no* hidden routing layer between
LLM and tool.

The fixture is deliberately tiny: a 'scripted provider' returns the
tool calls we want to see the LLM 'pick', then a final natural-language
answer. The planner is otherwise unmodified — same registry, same
executor, same audit trail — so any regression in the dispatch chain
is caught by these tests at integration level.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.planner import OpenAIPlanner
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.providers.base import ProviderToolCall, ProviderToolResponse


class _ScriptedProvider:
    """Return scripted ``ProviderToolResponse``s in order.

    Each scripted entry is either:
      * ``("tool", <ProviderToolCall>)`` — emit one tool call on this turn.
      * ``("text", <str>)`` — emit a final natural-language answer
        with no tool calls so the planner loop stops.

    The provider tracks which scripts the LLM 'saw' so a test can
    assert the full tool registry was made available on every turn.
    """

    def __init__(self, script: list[tuple[str, Any]]) -> None:
        self._script = list(script)
        self._call_index = 0
        self.tool_schemas_seen: list[list[dict[str, Any]]] = []

    def respond_with_tools(
        self,
        *,
        system_prompt: str,
        input_items: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None = None,
    ) -> ProviderToolResponse:
        self.tool_schemas_seen.append(tools)
        if self._call_index >= len(self._script):
            return ProviderToolResponse(
                text="all done",
                model="stub",
                response_id=f"r-end-{self._call_index}",
                tool_calls=[],
                raw={},
            )
        kind, payload = self._script[self._call_index]
        self._call_index += 1
        if kind == "tool":
            return ProviderToolResponse(
                text="",
                model="stub",
                response_id=f"r-tool-{self._call_index}",
                tool_calls=[payload],
                raw={},
            )
        return ProviderToolResponse(
            text=str(payload),
            model="stub",
            response_id=f"r-text-{self._call_index}",
            tool_calls=[],
            raw={},
        )


def _make_planner_and_executor(
    tmp: Path, provider: _ScriptedProvider
) -> tuple[OpenAIPlanner, AgentExecutor, Path]:
    trace_path = tmp / "agent_trace.jsonl"
    registry = AgentToolRegistry()
    executor = AgentExecutor(
        registry,
        session_dir=tmp,
        trace_path=trace_path,
        dry_run=False,
        profile=Profile.QUICK,
    )
    planner = OpenAIPlanner(
        provider=provider,  # type: ignore[arg-type]
        registry=registry,
        max_steps=3,
        verbose=False,
        emit=lambda text: None,
    )
    return planner, executor, trace_path


class LlmPicksSwmmAnywhereForBboxPromptTests(unittest.TestCase):
    """Smoke test: the swmm-anywhere skill is *visible* to the LLM.

    The legacy ``select_workflow_mode`` gate did not have a
    'synth-from-bbox' enum value, so a bbox-only prompt always fell
    through to a wrong mode. Post-refactor the typed tool surface is
    on the registry and the LLM can call it directly.
    """

    def test_bbox_prompt_dispatches_to_synth_swmm_from_bbox(self) -> None:
        # The provider scripts the LLM 'picking' synth_swmm_from_bbox.
        # The point of the test is not that the LLM picks the right
        # tool — that's an LLM-quality question — but that *when* it
        # does pick it, the call reaches the executor without a mode
        # gate in the middle. We stub the heavy SWMManywhere wrapper
        # so the test stays fast and does not require the [anywhere]
        # extra; the unit-level integration is covered by
        # ``tests/test_tool_handlers_swmm_anywhere.py``.
        provider = _ScriptedProvider(
            [
                (
                    "tool",
                    ProviderToolCall(
                        call_id="c1",
                        name="synth_swmm_from_bbox",
                        arguments={"bbox": [-0.05, 51.48, -0.04, 51.49]},
                    ),
                ),
                ("text", "Synthesised the model."),
            ]
        )

        from agentic_swmm.integrations import swmmanywhere_runner

        fake_result = swmmanywhere_runner.SynthRunResult(
            inp_path=Path("/tmp/fake.inp"),
            run_dir=Path("/tmp/run"),
            raw_manifest_path=Path("/tmp/run/00_raw/raw_manifest.json"),
            provenance={"tool": "swmmanywhere", "bbox_wgs84": [-0.05, 51.48, -0.04, 51.49]},
            stage_durations={"swmmanywhere_pipeline": 0.0},
            warnings=(),
        )

        with tempfile.TemporaryDirectory() as raw, mock.patch.object(
            swmmanywhere_runner, "run_synth_from_bbox", return_value=fake_result
        ):
            tmp = Path(raw)
            planner, executor, _ = _make_planner_and_executor(tmp, provider)
            outcome = planner.run(
                goal="use SWMManywhere to synthesise an INP for bbox -0.05,51.48,-0.04,51.49",
                session_dir=tmp,
                trace_path=tmp / "agent_trace.jsonl",
                executor=executor,
            )

        # The tool call reached the planner's plan ledger, which means
        # there was no mode gate intercepting it.
        synth_calls = [c for c in outcome.plan if c.name == "synth_swmm_from_bbox"]
        self.assertEqual(len(synth_calls), 1)
        self.assertEqual(
            synth_calls[0].args["bbox"],
            [-0.05, 51.48, -0.04, 51.49],
        )

    def test_synth_swmm_from_bbox_schema_is_visible_to_llm(self) -> None:
        """The LLM-facing tool registry must expose the new tool's
        schema on every provider turn — that's what 'LLM picks tools
        from descriptions' means in operational terms."""
        provider = _ScriptedProvider([("text", "no tool needed")])

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            planner, executor, _ = _make_planner_and_executor(tmp, provider)
            planner.run(
                goal="just chatting",
                session_dir=tmp,
                trace_path=tmp / "agent_trace.jsonl",
                executor=executor,
            )

        # Single LLM turn happened; tools list was sent.
        self.assertEqual(len(provider.tool_schemas_seen), 1)
        tool_names = {spec["name"] for spec in provider.tool_schemas_seen[0]}
        self.assertIn("synth_swmm_from_bbox", tool_names)
        # The legacy mode gate is gone.
        self.assertNotIn("select_workflow_mode", tool_names)


class LlmPicksRunSwmmInpForExistingInpTests(unittest.TestCase):
    """When the prompt references an existing INP path, the LLM picks
    ``run_swmm_inp`` directly — no ``select_workflow_mode`` hop. This
    locks the post-refactor flat-tool-registry shape."""

    def test_existing_inp_prompt_dispatches_to_run_swmm_inp(self) -> None:
        provider = _ScriptedProvider(
            [
                (
                    "tool",
                    ProviderToolCall(
                        call_id="c1",
                        name="run_swmm_inp",
                        arguments={"inp_path": "examples/tecnopolo/tecnopolo_r1_199401.inp"},
                    ),
                ),
                ("text", "Run complete."),
            ]
        )

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            planner, executor, _ = _make_planner_and_executor(tmp, provider)
            outcome = planner.run(
                goal="run examples/tecnopolo/tecnopolo_r1_199401.inp",
                session_dir=tmp,
                trace_path=tmp / "agent_trace.jsonl",
                executor=executor,
            )

        run_calls = [c for c in outcome.plan if c.name == "run_swmm_inp"]
        self.assertEqual(len(run_calls), 1)
        self.assertEqual(
            run_calls[0].args["inp_path"],
            "examples/tecnopolo/tecnopolo_r1_199401.inp",
        )

    def test_no_select_workflow_mode_call_appears_in_plan(self) -> None:
        """Even if the planner's introspection cluster fires
        (``list_skills`` etc.), the deleted ``select_workflow_mode``
        must never appear — the tool is no longer registered."""
        provider = _ScriptedProvider(
            [
                (
                    "tool",
                    ProviderToolCall(
                        call_id="c1",
                        name="run_swmm_inp",
                        arguments={"inp_path": "examples/tecnopolo/tecnopolo_r1_199401.inp"},
                    ),
                ),
                ("text", "ok"),
            ]
        )

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            planner, executor, _ = _make_planner_and_executor(tmp, provider)
            outcome = planner.run(
                goal="run examples/tecnopolo/tecnopolo_r1_199401.inp and plot it",
                session_dir=tmp,
                trace_path=tmp / "agent_trace.jsonl",
                executor=executor,
            )

        names_in_plan = {call.name for call in outcome.plan}
        self.assertNotIn("select_workflow_mode", names_in_plan)


class LlmCanSequenceMultipleToolsAcrossTurnsTests(unittest.TestCase):
    """Post-refactor the LLM is free to sequence tools across multiple
    turns without a mode adapter dictating the order. This test mocks
    a two-tool sequence (``inspect_plot_options`` → ``plot_run``) and
    asserts both reach the executor."""

    def test_two_tool_sequence_reaches_executor_in_order(self) -> None:
        provider = _ScriptedProvider(
            [
                (
                    "tool",
                    ProviderToolCall(
                        call_id="c1",
                        name="inspect_plot_options",
                        arguments={"run_dir": "runs/agent/sample"},
                    ),
                ),
                (
                    "tool",
                    ProviderToolCall(
                        call_id="c2",
                        name="capabilities",
                        arguments={},
                    ),
                ),
                ("text", "Done."),
            ]
        )

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            planner, executor, _ = _make_planner_and_executor(tmp, provider)
            outcome = planner.run(
                goal="inspect the run then describe what I can do next",
                session_dir=tmp,
                trace_path=tmp / "agent_trace.jsonl",
                executor=executor,
            )

        # Two tool calls landed; both came from the LLM, not from a
        # mode adapter.
        names_in_plan = [
            c.name
            for c in outcome.plan
            if c.name in {"inspect_plot_options", "capabilities"}
        ]
        self.assertEqual(names_in_plan, ["inspect_plot_options", "capabilities"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
