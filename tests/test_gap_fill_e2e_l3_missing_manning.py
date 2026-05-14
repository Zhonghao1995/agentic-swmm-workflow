"""End-to-end L3 scenario: missing Manning's n (PRD-GF-CORE).

The tool is invoked without ``manning_n_imperv`` and emits an L3
``gap_signal``. The proposer hits the registry
(``manning_n_imperv`` aliases to ``manning_n_paved``: 0.013, source
EPA SWMM 5 Manual Table 8-1). The (mocked) user accepts. The tool
retries with merged args and completes. Asserts:

- ``proposer.source == "registry"`` and ``confidence == "HIGH"``.
- ``final_value == 0.013``.
- A ``gap_fill_L3`` entry lands in ``experiment_provenance.json``.
- **No** ``llm_calls.jsonl`` entry is created (registry hit must NOT
  invoke an LLM — Done Criterion 5 of the PRD).
"""

from __future__ import annotations

import json
import os
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent.tool_registry import AgentToolRegistry, ToolSpec
from agentic_swmm.agent.types import ToolCall
from agentic_swmm.gap_fill.protocol import GapSignal, new_gap_id


class L3MissingManningE2ETests(unittest.TestCase):
    def test_registry_hit_resolves_without_llm(self) -> None:
        invocations: list[dict] = []

        def _build_handler(call: ToolCall, session_dir: Path) -> dict:
            invocations.append(dict(call.args))
            if "manning_n_imperv" not in call.args:
                sig = GapSignal(
                    gap_id=new_gap_id(),
                    severity="L3",
                    kind="param_value",
                    field="manning_n_imperv",
                    context={
                        "tool": call.name,
                        "step": "build_inp",
                        "workflow": "swmm-end-to-end",
                    },
                )
                return {
                    "tool": call.name,
                    "args": call.args,
                    "ok": False,
                    "summary": "missing Manning's n for impervious",
                    "gap_signal": sig.to_dict(),
                }
            return {
                "tool": call.name,
                "args": call.args,
                "ok": True,
                "summary": "INP built",
            }

        spec = ToolSpec(
            name="build_inp_mock_l3",
            description="mock build_inp emitting L3 gap",
            parameters={
                "type": "object",
                "properties": {"manning_n_imperv": {"type": "number"}},
                "additionalProperties": True,
            },
            handler=_build_handler,
            supports_gap_fill=True,
        )
        registry = AgentToolRegistry()
        registry._tools["build_inp_mock_l3"] = spec  # noqa: SLF001

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            call = ToolCall(name="build_inp_mock_l3", args={})

            # The UI runs in TTY mode; the user accepts the proposal.
            with mock.patch(
                "builtins.input", side_effect=["a"]
            ), mock.patch(
                "agentic_swmm.agent.runtime_loop._is_tty", return_value=True
            ):
                result = registry.execute(call, session_dir)

            self.assertTrue(result.get("ok"))
            gf = result["gap_filled"]
            self.assertEqual(len(gf), 1)
            self.assertEqual(gf[0]["field"], "manning_n_imperv")
            self.assertEqual(gf[0]["final_value"], 0.013)
            self.assertEqual(gf[0]["source"], "registry")

            # Two invocations: emit, retry.
            self.assertEqual(len(invocations), 2)
            self.assertNotIn("manning_n_imperv", invocations[0])
            self.assertEqual(invocations[1]["manning_n_imperv"], 0.013)

            # Ledger artefacts.
            ledger = session_dir / "09_audit" / "gap_decisions.json"
            payload = json.loads(ledger.read_text(encoding="utf-8"))
            decision = payload["decisions"][0]
            self.assertEqual(decision["severity"], "L3")
            self.assertEqual(decision["field"], "manning_n_imperv")
            self.assertEqual(decision["proposer"]["source"], "registry")
            self.assertEqual(decision["proposer"]["confidence"], "HIGH")
            self.assertEqual(decision["final_value"], 0.013)
            self.assertIn(
                "manning_n_paved", decision["proposer"]["registry_ref"]
            )

            # Cross-link entry in human_decisions.
            prov = session_dir / "09_audit" / "experiment_provenance.json"
            prov_payload = json.loads(prov.read_text(encoding="utf-8"))
            actions = [
                d["action"] for d in prov_payload["human_decisions"]
            ]
            self.assertIn("gap_fill_L3", actions)

            # Done Criterion 5: registry hit must NOT have invoked the
            # LLM proposer. The llm_calls.jsonl ledger must not exist
            # (or must be empty if some upstream code touched it).
            llm_log = session_dir / "09_audit" / "llm_calls.jsonl"
            if llm_log.is_file():
                self.assertEqual(llm_log.read_text(encoding="utf-8").strip(), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
