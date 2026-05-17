"""Issue #124 Part C: ``integrations/mcp/README.md`` count matches ``mcp/``.

The integration README enumerated 8 module MCP servers; the repo ships 11.
Three documented integrations (``swmm-experiment-audit``,
``swmm-modeling-memory``, ``swmm-uncertainty``) were invisible to a reader
of this README. This guard ensures the count stays in sync.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _count_mcp_servers() -> int:
    mcp_root = REPO_ROOT / "mcp"
    return sum(
        1
        for child in mcp_root.iterdir()
        if child.is_dir() and not child.name.startswith(".")
    )


_NUMBER_WORDS = {
    1: ("one",),
    2: ("two",),
    3: ("three",),
    4: ("four",),
    5: ("five",),
    6: ("six",),
    7: ("seven",),
    8: ("eight",),
    9: ("nine",),
    10: ("ten",),
    11: ("eleven",),
    12: ("twelve",),
}


class IntegrationsMcpReadmeCountTests(unittest.TestCase):
    def test_readme_servers_count_matches_disk(self) -> None:
        readme_path = REPO_ROOT / "integrations" / "mcp" / "README.md"
        text = readme_path.read_text(encoding="utf-8").lower()
        actual = _count_mcp_servers()
        expected_word = _NUMBER_WORDS.get(actual, (str(actual),))[0]
        expected_digit = str(actual)
        pattern = rf"\b({re.escape(expected_word)}|{re.escape(expected_digit)})\s+module mcp servers\b"
        self.assertRegex(
            text,
            pattern,
            f"integrations/mcp/README.md must mention {expected_word!r} or "
            f"{expected_digit!r} module MCP servers (found {actual} on disk).",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
