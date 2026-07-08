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

from tempfile import TemporaryDirectory

from agentic_swmm.agent.tool_handlers.swmm_audit import _audit_run_args
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.agent.types import ToolCall

_SESSION = Path("/tmp/session")


def _mapped(extra: dict) -> dict:
    """Run the mapper against a REAL temp run dir: since the ADR-0004
    fix the mapper resolves run_dir to an absolute existing directory
    (a relative path used to cross the MCP boundary raw and land
    09_audit/ inside the server's cwd)."""
    with TemporaryDirectory() as raw:
        return _audit_run_args(
            ToolCall("audit_run", {"run_dir": raw, **extra}), _SESSION
        )


class AuditRunObsidianDefaultTests(unittest.TestCase):
    def test_bare_call_injects_no_obsidian(self) -> None:
        self.assertIs(_mapped({}).get("noObsidian"), True)

    def test_explicit_opt_in_allows_vault_write(self) -> None:
        self.assertNotIn("noObsidian", _mapped({"obsidian": True}))

    def test_falsy_opt_in_still_injects_no_obsidian(self) -> None:
        """obsidian=false must behave exactly like omitting the arg."""
        self.assertIs(_mapped({"obsidian": False}).get("noObsidian"), True)

    def test_relative_run_dir_resolves_to_absolute_before_mcp(self) -> None:
        """The ADR-0004 regression lock: whatever reaches the MCP layer
        must be absolute so the Node server's cwd never matters."""
        with TemporaryDirectory() as raw:
            payload = _audit_run_args(ToolCall("audit_run", {"run_dir": raw}), _SESSION)
        self.assertTrue(Path(payload["runDir"]).is_absolute())

    def test_missing_run_dir_fails_soft(self) -> None:
        payload = _audit_run_args(
            ToolCall("audit_run", {"run_dir": "/nonexistent/run-dir"}), _SESSION
        )
        self.assertFalse(payload.get("ok", False))


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
