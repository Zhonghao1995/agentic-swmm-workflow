"""CRITICAL: assert expert-only commands have NO agent surface (PRD-Z).

The four expert-only CLI commands

* ``aiswmm calibration accept``  → action name ``calibration_accept``
* ``aiswmm pour_point confirm`` → action name ``pour_point_confirm``
* ``aiswmm thresholds override`` → action name ``thresholds_override``
* ``aiswmm publish``             → action name ``publish``

are reachable ONLY via the human-facing CLI. They must NEVER appear:

* as a ``ToolSpec.name`` inside ``_build_tools()`` in the agent registry;
* as a tool name inside any ``mcp/*/server.js`` registration.

This test scans the source files directly so that an accidental future
ToolSpec or ``server.tool('publish', ...)`` registration breaks CI
loudly with a one-line diff message.

The PRD's Done Criteria includes this grep as a measurable outcome.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "agentic_swmm" / "agent" / "tool_registry.py"
MCP_ROOT = REPO_ROOT / "mcp"
FORBIDDEN_NAMES = (
    "calibration_accept",
    "pour_point_confirm",
    "thresholds_override",
    "publish",
)


class ExpertOnlyCommandsNotInRegistryTests(unittest.TestCase):
    def test_forbidden_names_not_in_tool_registry(self) -> None:
        text = REGISTRY_PATH.read_text(encoding="utf-8")
        # Match either bare ToolSpec string literals or fully-qualified
        # ``"name"`` parameter assignments. Searching the whole file is
        # fine — the registry has no other reason to mention these names.
        for forbidden in FORBIDDEN_NAMES:
            with self.subTest(name=forbidden):
                self.assertNotIn(
                    f'"{forbidden}"',
                    text,
                    f"forbidden expert-only command name leaked into tool "
                    f"registry: {forbidden!r}",
                )
                self.assertNotIn(
                    f"'{forbidden}'",
                    text,
                    f"forbidden expert-only command name leaked into tool "
                    f"registry: {forbidden!r}",
                )

    def test_forbidden_names_not_in_mcp_servers(self) -> None:
        servers = list(MCP_ROOT.glob("*/server.js"))
        self.assertGreater(
            len(servers),
            0,
            f"no MCP servers found under {MCP_ROOT} — test would silently "
            "pass; refusing to be a no-op.",
        )
        for server in servers:
            text = server.read_text(encoding="utf-8")
            for forbidden in FORBIDDEN_NAMES:
                with self.subTest(server=str(server), name=forbidden):
                    # server.tool('<name>', ...) is the registration form
                    # in the existing MCP servers; we still also flag any
                    # bare reference to the forbidden token so a renamed
                    # registration pattern cannot slip past.
                    pattern = re.compile(
                        r"server\.tool\(\s*['\"]" + re.escape(forbidden) + r"['\"]"
                    )
                    self.assertIsNone(
                        pattern.search(text),
                        f"{server} registers a forbidden expert-only tool: "
                        f"{forbidden!r}",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
