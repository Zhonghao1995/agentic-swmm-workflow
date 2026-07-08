"""``audit_run``'s agent path defaults to no vault writes (issue #328).

The audit script's own default writes the modelling note into the
user's real Obsidian vault (``~/Documents/Agentic-SWMM-Obsidian-Vault``),
while the CLI verb defaults to ``--no-obsidian``. The agent path now
matches the CLI: the args mapper injects ``noObsidian`` unless the
caller explicitly opts in with ``obsidian=true``, so a bare agent-driven
audit never has side effects outside the run directory.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from agentic_swmm.agent.tool_handlers.swmm_audit import _audit_run_args
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall

_SESSION = Path("/tmp/session")


class AuditRunObsidianDefaultTests(unittest.TestCase):
    def test_bare_call_injects_no_obsidian(self) -> None:
        payload = _audit_run_args(ToolCall("audit_run", {"run_dir": "runs/agent/x"}), _SESSION)
        self.assertIs(payload.get("noObsidian"), True)

    def test_explicit_opt_in_allows_vault_write(self) -> None:
        payload = _audit_run_args(
            ToolCall("audit_run", {"run_dir": "runs/agent/x", "obsidian": True}), _SESSION
        )
        self.assertNotIn("noObsidian", payload)

    def test_falsy_opt_in_still_injects_no_obsidian(self) -> None:
        """obsidian=false must behave exactly like omitting the arg."""
        payload = _audit_run_args(
            ToolCall("audit_run", {"run_dir": "runs/agent/x", "obsidian": False}), _SESSION
        )
        self.assertIs(payload.get("noObsidian"), True)


class AuditRunToolSpecTests(unittest.TestCase):
    def test_toolspec_exposes_obsidian_opt_in(self) -> None:
        schema = next(
            s for s in AgentToolRegistry().schemas() if s["name"] == "audit_run"
        )
        properties = schema["parameters"]["properties"]
        self.assertIn("obsidian", properties)
        self.assertEqual(properties["obsidian"]["type"], "boolean")
        # The description must tell the planner the default is side-effect
        # free so it never assumes vault mirroring happened implicitly.
        self.assertIn("Default false", properties["obsidian"]["description"])


class ServerForwardsNoObsidianTests(unittest.TestCase):
    def test_server_js_accepts_and_forwards_the_flag(self) -> None:
        """Grep-level contract on mcp/swmm-experiment-audit/server.js:
        the zod schema accepts ``noObsidian`` and the spawn arg list
        forwards ``--no-obsidian`` (same strength as
        tests/test_mcp_toolname_contract.py)."""
        server = Path(__file__).resolve().parents[1] / "mcp" / "swmm-experiment-audit" / "server.js"
        source = server.read_text(encoding="utf-8")
        self.assertIn("noObsidian", source)
        self.assertIn("'--no-obsidian'", source)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
