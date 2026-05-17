"""Issue #122: AST regression guard extension — scan ``agent/config/intent_map.json``.

The PR #118 / PR #119 AST guard walks ``.py`` files but skips JSON config. The
intent map's ``swmm_request_keywords`` array still contained ``"tecnopolo"`` as
of v0.6.2-alpha; that token is dead vocabulary now that ``_match_registered_case``
routes watershed-by-name through ``case_registry.list_cases()``. This test
walks every string-typed value in ``agent/config/intent_map.json`` and asserts
no known example-watershed slug appears anywhere — the single defensible
boundary for portability becomes "the case registry plus this guard".
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
_FORBIDDEN_NAMES = ("tecnopolo", "todcreek", "tod-creek", "tod creek")


def _walk_strings(node):
    """Yield every string leaf in a JSON-decoded structure."""
    if isinstance(node, str):
        yield node
        return
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
        return
    if isinstance(node, list):
        for value in node:
            yield from _walk_strings(value)
        return


class IntentMapNoHardcodedWatershedsTests(unittest.TestCase):
    def test_intent_map_contains_no_watershed_slug(self) -> None:
        config_path = REPO_ROOT / "agent" / "config" / "intent_map.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        hits: list[str] = []
        for value in _walk_strings(config):
            lowered = value.lower()
            for name in _FORBIDDEN_NAMES:
                if name in lowered:
                    hits.append(f"{value!r}")
        self.assertEqual(
            hits,
            [],
            "agent/config/intent_map.json contains hardcoded watershed token(s):\n"
            + "\n".join(hits)
            + "\n\nWatershed routing is now case-registry-driven (PR #119); "
            "remove the slug(s) and rely on cases/<id>/case_meta.yaml instead.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
