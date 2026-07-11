"""Surface ratchets (ADR-0006 D1/D3): growth becomes a decision, not drift.

Two pinned lists turn silent surface growth into a failing test:

* the registered CLI verbs (they grew 28 -> 35 with nobody deciding), and
* the MCP-routed tool set (style (a) is FROZEN: each addition costs 6-9
  files across three languages and three hand-maintained tables).

Editing a pinned list is allowed and expected: do it in the same PR as
the change, citing the ADR note that justifies the growth. What these
tests forbid is the surface growing without anyone noticing.
"""
from __future__ import annotations

import unittest

# ADR-0006 D3: the CLI verb surface as of acceptance. Adding a verb?
# Great: add it here IN THE SAME PR with a one-line justification in
# the PR body. Removing one: same deal (aliases stay in the router).
PINNED_CLI_VERBS = [
    "agent", "audit", "bootstrap", "calibrate", "calibration",
    "capabilities", "case", "cite", "cite-param", "compare", "config",
    "demo", "doctor", "gap", "help", "list", "login", "map", "mcp",
    "memory", "model", "plot", "pour_point", "publish", "report",
    "review", "run", "runs", "setup", "skill", "storm", "thresholds",
    "trace", "transfer", "uncertainty",
]

# ADR-0006 D1: style (a) MCP-routed is FROZEN at these 21 tools. New
# capability takes the golden path (in-process typed tool) or the
# sanctioned dark-server path; growing THIS list requires an ADR.
PINNED_MCP_ROUTED_TOOLS = [
    "audit_run", "build_inp", "build_raingage_section", "format_rainfall",
    "generate_design_storm", "network_qa", "network_to_inp", "plot_run",
    "run_swmm_inp", "summarize_memory", "swmm_calibrate",
    "swmm_calibrate_dream_zs", "swmm_calibrate_sceua",
    "swmm_calibrate_search", "swmm_rainfall_ensemble",
    "swmm_sensitivity_morris", "swmm_sensitivity_oat",
    "swmm_sensitivity_scan", "swmm_sensitivity_sobol",
    "swmm_uncertainty_source_decomposition", "swmm_validate",
]


class CliVerbRatchetTests(unittest.TestCase):
    def test_registered_verbs_match_the_pinned_list(self) -> None:
        from agentic_swmm.cli import registered_commands

        actual = sorted(registered_commands())
        self.assertEqual(
            actual,
            sorted(PINNED_CLI_VERBS),
            "CLI verb surface changed. If deliberate, update "
            "PINNED_CLI_VERBS in this file IN THE SAME PR and justify "
            "the change in the PR body (ADR-0006 D3).",
        )


class McpRoutedFreezeTests(unittest.TestCase):
    def test_mcp_routed_set_is_frozen(self) -> None:
        from agentic_swmm.agent.tool_registry import AgentToolRegistry

        registry = AgentToolRegistry()
        actual = sorted(n for n in registry.names if registry.mcp_routing(n))
        self.assertEqual(
            actual,
            sorted(PINNED_MCP_ROUTED_TOOLS),
            "The MCP-routed (style a) tool set changed. That style is "
            "FROZEN by ADR-0006 D1: new capability uses the in-process "
            "golden path or a dark server. Growing this list requires "
            "an ADR reference in the same PR.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
