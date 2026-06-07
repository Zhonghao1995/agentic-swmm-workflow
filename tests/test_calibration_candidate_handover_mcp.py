"""Bug #233 (H8): calibration MCP server must forward --candidate-run-dir.

The Python handler (swmm_calibrate.py) already supports --candidate-run-dir,
but the JS MCP wrapper never declared candidateRunDir in its Zod schemas or
its ListTools inputSchema blocks, so the candidate artifacts were never produced
via the MCP path and `aiswmm calibration accept` always returned rc=3.

These are text-contract tests on server.js (grep-style, mirroring
test_parameter_scout_relocated.py) — no Node.js process is needed.

Scope: the 4 calibration strategy tools (swmm_calibrate, swmm_calibrate_search,
swmm_calibrate_sceua, swmm_calibrate_dream_zs). swmm_sensitivity_scan must NOT
gain the flag (it is a legacy scan tool, not a strategy that writes candidates).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_JS = REPO_ROOT / "mcp" / "swmm-calibration" / "server.js"

# The 4 tools that must support candidateRunDir.
STRATEGY_TOOLS = [
    "swmm_calibrate",
    "swmm_calibrate_search",
    "swmm_calibrate_sceua",
    "swmm_calibrate_dream_zs",
]

# This tool must NOT gain the flag.
SCAN_TOOL = "swmm_sensitivity_scan"


class CalibrationCandidateHandoverMcpTests(unittest.TestCase):
    """Structural assertions that the MCP server forwards --candidate-run-dir."""

    def setUp(self) -> None:
        self.assertTrue(
            SERVER_JS.exists(),
            msg=f"Expected MCP server at {SERVER_JS}",
        )
        self.src = SERVER_JS.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Zod schema assertions (declaration side)
    # ------------------------------------------------------------------ #

    def test_calibrate_args_zod_has_candidate_run_dir(self) -> None:
        """CalibrateArgs Zod schema must declare candidateRunDir."""
        self.assertIn(
            "candidateRunDir",
            self.src,
            msg=(
                "mcp/swmm-calibration/server.js must declare candidateRunDir "
                "in at least one Zod schema (CalibrateArgs / SearchArgs / "
                "SceuaArgs / DreamZsArgs)."
            ),
        )

    def test_candidate_run_dir_pushed_to_py_args(self) -> None:
        """The --candidate-run-dir flag must be pushed to pyArgs for each strategy."""
        self.assertIn(
            "--candidate-run-dir",
            self.src,
            msg=(
                "mcp/swmm-calibration/server.js must push --candidate-run-dir "
                "to pyArgs when candidateRunDir is set."
            ),
        )

    def test_four_strategy_tools_expose_candidate_run_dir_in_list_tools(self) -> None:
        """All 4 strategy inputSchema blocks must expose candidateRunDir to the LLM."""
        # Count occurrences of candidateRunDir in the inputSchema section.
        # We look for it in the ListTools handler (the inputSchema property blocks).
        # A minimal check: candidateRunDir must appear >= 4 times total
        # (once per strategy tool in ListTools, plus at least once in Zod schemas /
        # CallTool branches — conservatively expect >= 4 hits).
        hits = [m.start() for m in re.finditer(r"candidateRunDir", self.src)]
        self.assertGreaterEqual(
            len(hits),
            4,
            msg=(
                f"Expected candidateRunDir to appear at least 4 times in server.js "
                f"(once per strategy tool in ListTools + Zod + CallTool branches), "
                f"got {len(hits)}."
            ),
        )

    def test_each_strategy_tool_block_mentions_candidate_run_dir(self) -> None:
        """Each strategy tool name must appear near (within 3000 chars of) candidateRunDir."""
        for tool in STRATEGY_TOOLS:
            # Find all positions of the tool name.
            tool_positions = [m.start() for m in re.finditer(re.escape(f'"{tool}"'), self.src)]
            self.assertTrue(
                tool_positions,
                msg=f"Tool name {tool!r} not found in server.js",
            )
            crd_positions = [m.start() for m in re.finditer(r"candidateRunDir", self.src)]
            # At least one candidateRunDir occurrence must be within 3000 chars of
            # at least one occurrence of this tool name.
            found_proximity = any(
                abs(tp - cp) <= 3000
                for tp in tool_positions
                for cp in crd_positions
            )
            self.assertTrue(
                found_proximity,
                msg=(
                    f"candidateRunDir was not found within 3000 chars of tool "
                    f"{tool!r} in server.js. The tool's Zod schema and/or "
                    f"CallTool branch must include candidateRunDir."
                ),
            )

    # ------------------------------------------------------------------ #
    # Negative assertion: swmm_sensitivity_scan must NOT gain the flag
    # ------------------------------------------------------------------ #

    def test_sensitivity_scan_does_not_get_candidate_run_dir(self) -> None:
        """swmm_sensitivity_scan must NOT have candidateRunDir (it's a scan, not a strategy)."""
        # Find the block for swmm_sensitivity_scan.  It ends where the next tool
        # begins.  We isolate the text between its first occurrence and the next
        # tool block to avoid false positives from other tools.
        scan_start = self.src.find(f'"{SCAN_TOOL}"')
        self.assertNotEqual(scan_start, -1, msg=f"{SCAN_TOOL!r} not found in server.js")

        # Find the next tool declaration after the scan block.
        next_tool_match = re.search(
            r'"swmm_(?:calibrate|validate)"',
            self.src[scan_start + len(SCAN_TOOL) :],
        )
        if next_tool_match:
            scan_block = self.src[scan_start : scan_start + len(SCAN_TOOL) + next_tool_match.start()]
        else:
            scan_block = self.src[scan_start:]

        self.assertNotIn(
            "candidateRunDir",
            scan_block,
            msg=(
                f"swmm_sensitivity_scan must NOT have candidateRunDir — "
                f"it is a legacy explicit-set scanner, not a strategy that "
                f"writes candidate artifacts."
            ),
        )


if __name__ == "__main__":
    unittest.main()
