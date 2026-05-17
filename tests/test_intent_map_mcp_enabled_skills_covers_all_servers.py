"""Issue #124 Part D: ``mcp_enabled_skills`` must cover every shipped MCP server.

``select_relevant_mcp_servers`` (in ``intent_map``) intersects skill names
against the ``mcp_enabled_skills`` set so the planner only proactively
introspects servers it knows are MCP-wired. The repo ships 11 ``mcp/<name>/``
servers but the config listed only 8 of them; ``swmm-experiment-audit``,
``swmm-modeling-memory``, and ``swmm-uncertainty`` were reachable through
their ToolSpec routing but the planner never asked them to enumerate their
tool schemas.

This guard asserts the set in the config is a superset of the directories in
``mcp/`` so a future MCP server can't slip in without being added here.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class IntentMapMcpEnabledSkillsCoversAllServersTests(unittest.TestCase):
    def test_mcp_enabled_skills_contains_every_shipped_mcp_server(self) -> None:
        config_path = REPO_ROOT / "agent" / "config" / "intent_map.json"
        mcp_root = REPO_ROOT / "mcp"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        enabled = set(config.get("mcp_enabled_skills") or [])

        shipped: set[str] = set()
        for child in mcp_root.iterdir():
            if not child.is_dir():
                continue
            # node_modules and similar tooling dirs are not MCP servers.
            if child.name.startswith("."):
                continue
            shipped.add(child.name)

        missing = shipped - enabled
        self.assertEqual(
            missing,
            set(),
            "mcp_enabled_skills must list every MCP server shipped under mcp/; "
            f"missing: {sorted(missing)}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
