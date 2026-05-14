"""End-to-end L1 scenario: missing rainfall file (PRD-GF-CORE).

A workflow declares it needs a rainfall file. The pre-flight scanner
catches that the path does not exist. The (mocked) UI picks a real
file from the filesystem; the tool retries with the merged args and
completes. Asserts:

- ``gap_decisions.json`` contains the L1 record with the correct
  final_value (user's pick).
- ``experiment_provenance.json`` carries a matching
  ``gap_fill_L1`` ``human_decisions`` entry.
- The tool was re-invoked with the resolved path in ``args``.
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


class L1MissingRainfallE2ETests(unittest.TestCase):
    def test_pre_flight_catches_and_resumes(self) -> None:
        invocations: list[dict] = []

        def _build_handler(call: ToolCall, session_dir: Path) -> dict:
            invocations.append(dict(call.args))
            # If the rainfall_file path exists, succeed.
            path = call.args.get("rainfall_file")
            if path and Path(path).is_file():
                return {
                    "tool": call.name,
                    "args": call.args,
                    "ok": True,
                    "summary": "INP built",
                }
            # Should not be reached if pre-flight is wired correctly.
            return {
                "tool": call.name,
                "args": call.args,
                "ok": False,
                "summary": "rainfall file missing (handler-level)",
            }

        spec = ToolSpec(
            name="build_inp_mock",
            description="mock build_inp with required rainfall",
            parameters={
                "type": "object",
                "properties": {"rainfall_file": {"type": "string"}},
                "required": ["rainfall_file"],
            },
            handler=_build_handler,
            supports_gap_fill=True,
            required_file_args=("rainfall_file",),
        )

        registry = AgentToolRegistry()
        registry._tools["build_inp_mock"] = spec  # noqa: SLF001

        with TemporaryDirectory() as tmp:
            session_dir = Path(tmp)
            # Create a real candidate rainfall file the user will pick.
            cases_dir = session_dir / "cases" / "case-a"
            cases_dir.mkdir(parents=True)
            rainfall_path = cases_dir / "rain.csv"
            rainfall_path.write_text(
                "timestamp,flow\n2020-01-01,1.0\n", encoding="utf-8"
            )

            # The planner passes a bogus initial path — the user will
            # be asked to supply a real one via the form.
            bogus = session_dir / "does_not_exist.csv"
            call = ToolCall(
                name="build_inp_mock",
                args={"rainfall_file": str(bogus)},
            )

            # Mock the form interaction so the user picks the real
            # rainfall file. The UI prompts: enter path.
            with mock.patch(
                "builtins.input", side_effect=[str(rainfall_path)]
            ), mock.patch(
                "agentic_swmm.agent.runtime_loop._is_tty", return_value=True
            ):
                result = registry.execute(call, session_dir)

            self.assertTrue(result.get("ok"))
            self.assertIn("gap_filled", result)
            gf = result["gap_filled"]
            self.assertEqual(len(gf), 1)
            self.assertEqual(gf[0]["field"], "rainfall_file")
            self.assertEqual(gf[0]["final_value"], str(rainfall_path))

            # The handler was invoked exactly once (after the pre-flight
            # resolution; the bogus path never reached it).
            self.assertEqual(len(invocations), 1)
            self.assertEqual(
                invocations[0]["rainfall_file"], str(rainfall_path)
            )

            # Ledger artefacts.
            ledger = session_dir / "09_audit" / "gap_decisions.json"
            self.assertTrue(ledger.is_file())
            payload = json.loads(ledger.read_text(encoding="utf-8"))
            decisions = payload["decisions"]
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["severity"], "L1")
            self.assertEqual(decisions[0]["field"], "rainfall_file")
            self.assertEqual(
                decisions[0]["final_value"], str(rainfall_path)
            )

            prov = session_dir / "09_audit" / "experiment_provenance.json"
            self.assertTrue(prov.is_file())
            prov_payload = json.loads(prov.read_text(encoding="utf-8"))
            actions = [
                d["action"] for d in prov_payload["human_decisions"]
            ]
            self.assertIn("gap_fill_L1", actions)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
