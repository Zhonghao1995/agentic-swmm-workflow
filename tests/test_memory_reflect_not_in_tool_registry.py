"""CRITICAL: assert ``memory_reflect`` is NOT an agent or MCP tool (ME-3).

PRD memory-evolution-with-forgetting positions ``aiswmm memory reflect``
as an *expert-only* CLI mirroring the PRD-Z calibration-accept pattern
(issue #54): the agent may discover the command exists by reading the
source, but it must NEVER be able to invoke it. The justification is
governance — reflection rewrites the modeller's lessons file, and only
the human modeller has the authority to ratify those rewrites.

This test scans:

* ``agentic_swmm/agent/tool_registry.py`` — no ``ToolSpec`` may use the
  string ``"memory_reflect"`` as its name.
* ``mcp/*/server.js`` — no ``server.tool('memory_reflect', ...)`` line
  may appear.

The grep is intentionally a literal substring scan against the file
text so that any future ``ToolSpec(name="memory_reflect", ...)`` or
``server.tool("memory_reflect", ...)`` registration trips CI loudly
with a one-line diff message.

The PRD's Done Criteria includes this grep as a measurable outcome.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "agentic_swmm" / "agent" / "tool_registry.py"
MCP_ROOT = REPO_ROOT / "mcp"
FORBIDDEN_NAME = "memory_reflect"


class MemoryReflectNotInToolRegistryTests(unittest.TestCase):
    def test_memory_reflect_not_in_tool_registry(self) -> None:
        text = REGISTRY_PATH.read_text(encoding="utf-8")
        # Reject both bare ``"memory_reflect"`` and ``'memory_reflect'`` —
        # quote style alone should not be a loophole.
        self.assertNotIn(
            f'"{FORBIDDEN_NAME}"',
            text,
            f"forbidden expert-only command name leaked into tool "
            f"registry: {FORBIDDEN_NAME!r}",
        )
        self.assertNotIn(
            f"'{FORBIDDEN_NAME}'",
            text,
            f"forbidden expert-only command name leaked into tool "
            f"registry: {FORBIDDEN_NAME!r}",
        )

    def test_memory_reflect_not_in_mcp_servers(self) -> None:
        servers = list(MCP_ROOT.glob("*/server.js"))
        self.assertGreater(
            len(servers),
            0,
            f"no MCP servers found under {MCP_ROOT} — test would silently "
            "pass; refusing to be a no-op.",
        )
        for server in servers:
            text = server.read_text(encoding="utf-8")
            with self.subTest(server=str(server)):
                # ``server.tool('<name>', ...)`` is the registration form
                # used in every existing MCP server; rejecting it covers
                # the live surface. A bare-substring extra check below
                # catches a renamed registration helper too.
                pattern = re.compile(
                    r"server\.tool\(\s*['\"]"
                    + re.escape(FORBIDDEN_NAME)
                    + r"['\"]"
                )
                self.assertIsNone(
                    pattern.search(text),
                    f"{server} registers a forbidden expert-only tool: "
                    f"{FORBIDDEN_NAME!r}",
                )
                self.assertNotIn(
                    f'"{FORBIDDEN_NAME}"',
                    text,
                    f"{server} mentions forbidden expert-only token: "
                    f"{FORBIDDEN_NAME!r}",
                )
                self.assertNotIn(
                    f"'{FORBIDDEN_NAME}'",
                    text,
                    f"{server} mentions forbidden expert-only token: "
                    f"{FORBIDDEN_NAME!r}",
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
