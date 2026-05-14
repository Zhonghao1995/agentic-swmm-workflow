"""Integration test for the gap-fill runtime wiring (PRD-GF-CORE).

This exercises the full ``tool_registry.execute`` interception path
without any LLM / SWMM dependencies:

1. A mock tool with ``supports_gap_fill=True`` emits ``ok=false``
   with a ``gap_signal`` payload on the first call.
2. The runtime intercepts, proposes (mocked to return a registry hit),
   the UI auto-accepts (env-var driven for the test), the recorder
   writes both ledgers, and the tool is re-invoked.
3. The second invocation receives the merged args, returns ``ok=true``,
   and the runtime augments the result with ``gap_filled: [...]``.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolSpec
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.gap_fill.protocol import GapSignal, new_gap_id


class _ToolCounter:
    """Tiny stateful handler — first call emits gap, second succeeds."""

    def __init__(self, gap_field: str) -> None:
        self.gap_field = gap_field
        self.calls: list[ToolCall] = []

    def __call__(self, call: ToolCall, session_dir: Path) -> dict[str, object]:
        self.calls.append(call)
        if self.gap_field not in call.args:
            # Emit an L3 gap signal — exactly what swmm_calibrate /
            # parameter_recommender will look like once they're wired.
            sig = GapSignal(
                gap_id=new_gap_id(),
                severity="L3",
                kind="param_value",
                field=self.gap_field,
                context={"tool": call.name, "step": "build"},
            )
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "gap_signal": sig.to_dict(),
                "summary": "missing parameter; emitting gap_signal",
            }
        return {
            "tool": call.name,
            "args": call.args,
            "ok": True,
            "summary": "built with all parameters",
        }


class GapFillRuntimeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        # Force the non-TTY auto-approve path so the test is
        # deterministic without monkey-patching stdin.
        self._saved = os.environ.get("AISWMM_HITL_AUTO_APPROVE")
        os.environ["AISWMM_HITL_AUTO_APPROVE"] = "1"

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("AISWMM_HITL_AUTO_APPROVE", None)
        else:
            os.environ["AISWMM_HITL_AUTO_APPROVE"] = self._saved

    def test_l3_gap_signal_intercepted_and_retried(self) -> None:
        handler = _ToolCounter(gap_field="manning_n_imperv")
        spec = ToolSpec(
            name="mock_build",
            description="mock builder tool",
            parameters={
                "type": "object",
                "properties": {"manning_n_imperv": {"type": "number"}},
                "additionalProperties": True,
            },
            handler=handler,
            supports_gap_fill=True,
        )

        registry = AgentToolRegistry()
        registry._tools["mock_build"] = spec  # noqa: SLF001 — test seam

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            call = ToolCall(name="mock_build", args={})
            result = registry.execute(call, session_dir)

            # Tool was called twice: once with gap, once with merged args.
            self.assertEqual(len(handler.calls), 2)
            self.assertNotIn("manning_n_imperv", handler.calls[0].args)
            self.assertIn("manning_n_imperv", handler.calls[1].args)
            self.assertEqual(handler.calls[1].args["manning_n_imperv"], 0.013)

            # Final result is ok=true with gap_filled list.
            self.assertTrue(result.get("ok"))
            self.assertIn("gap_filled", result)
            gap_filled = result["gap_filled"]
            self.assertEqual(len(gap_filled), 1)
            self.assertEqual(gap_filled[0]["field"], "manning_n_imperv")
            self.assertEqual(gap_filled[0]["final_value"], 0.013)
            self.assertEqual(gap_filled[0]["source"], "registry")

            # Ledger artefacts created.
            gap_ledger = session_dir / "09_audit" / "gap_decisions.json"
            prov = session_dir / "09_audit" / "experiment_provenance.json"
            self.assertTrue(gap_ledger.is_file())
            self.assertTrue(prov.is_file())
            prov_payload = json.loads(prov.read_text(encoding="utf-8"))
            actions = [d["action"] for d in prov_payload["human_decisions"]]
            self.assertIn("gap_fill_L3", actions)

    def test_non_gap_fill_tool_passes_through(self) -> None:
        """Tools without ``supports_gap_fill`` are not wrapped."""

        def _handler(call: ToolCall, session_dir: Path) -> dict[str, object]:
            return {"tool": call.name, "args": call.args, "ok": True}

        spec = ToolSpec(
            name="plain_tool",
            description="plain tool",
            parameters={"type": "object", "properties": {}},
            handler=_handler,
            supports_gap_fill=False,
        )
        registry = AgentToolRegistry()
        registry._tools["plain_tool"] = spec  # noqa: SLF001

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            call = ToolCall(name="plain_tool", args={"k": "v"})
            result = registry.execute(call, session_dir)
        self.assertTrue(result["ok"])
        self.assertNotIn("gap_filled", result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
