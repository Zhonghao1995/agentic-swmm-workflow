"""Contract tests: calibration MCP tools accept an INLINE search space.

Bug (found live 2026-08-09, NL sweep): ``swmm_calibrate_search`` /
``_sceua`` / ``_dream_zs`` required ``searchSpace`` as a FILE PATH, so
a natural-language request with inline bounds ("manning_n 0.01-0.03")
forced the planner to create a JSON file first — and the evidence
guard rightly blocks freehand writes into run dirs, dead-ending the
whole calibration flow (observed: the planner burned its failure
budget trying apply_patch/run_allowed_command workarounds).

Fix under test (text-contract style, mirroring
``test_calibration_candidate_handover_mcp``): the server declares a
string-or-object union, materializes inline objects to
``<runRoot>/search_space.json`` (the space used becomes run evidence),
routes all three dispatch sites through the materializer, and
advertises both forms in the tool schemas.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_JS = REPO_ROOT / "mcp" / "swmm-calibration" / "server.js"

SEARCH_TOOLS = ["SearchArgs", "SceuaArgs", "DreamZsArgs"]


class InlineSearchSpaceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.src = SERVER_JS.read_text(encoding="utf-8")

    def test_union_input_declared(self) -> None:
        self.assertIn(
            "const SearchSpaceInput = z.union([z.string(), z.record(z.any())]);",
            self.src,
        )

    def test_all_three_arg_schemas_use_the_union(self) -> None:
        for args_name in SEARCH_TOOLS:
            block = re.search(
                rf"const {args_name} = Common\.extend\(\{{\n  searchSpace: (\w+),",
                self.src,
            )
            self.assertIsNotNone(block, args_name)
            self.assertEqual(block.group(1), "SearchSpaceInput", args_name)

    def test_materializer_writes_into_run_root(self) -> None:
        self.assertIn("function materializeSearchSpace(a)", self.src)
        self.assertIn('path.join(a.runRoot, "search_space.json")', self.src)

    def test_dispatch_sites_use_materializer(self) -> None:
        self.assertEqual(
            self.src.count('"--search-space", materializeSearchSpace(a)'), 3
        )
        # No dispatch site may pass the raw arg through any more.
        self.assertNotIn('"--search-space", a.searchSpace', self.src)

    def test_advertised_schemas_offer_both_forms(self) -> None:
        self.assertEqual(
            self.src.count('oneOf: [ { type: "string" }, { type: "object" } ]'),
            3,
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
