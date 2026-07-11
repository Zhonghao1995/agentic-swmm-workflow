"""Guard: runtime skill-directory lookups must be resource-aware.

The wheel installs skills/ into the packaged data dir
(<prefix>/aiswmm/skills), NOT next to site-packages, so a bare
``repo_root() / "skills"`` works in a source checkout and silently
misses on a pip install. ``resource_root()`` handles both; this guard
fails the build when a new call site takes the shortcut (the fifth
occurrence of this exact bug class was found while wiring the real
calibration engine, ADR-0005).
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "agentic_swmm"
# paths.py itself defines the resolvers and may reference the pattern.
ALLOWED = {PKG / "utils" / "paths.py"}
_PATTERN = re.compile(r"repo_root\(\)\s*/\s*[\'\"]skills[\'\"]")


class SkillsResolutionGuardTests(unittest.TestCase):
    def test_no_repo_root_skills_shortcuts(self) -> None:
        offenders = []
        for path in PKG.rglob("*.py"):
            if path in ALLOWED:
                continue
            if _PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(str(path.relative_to(PKG.parent)))
        self.assertEqual(
            offenders,
            [],
            "skills lookups must use resource_root() so pip installs "
            f"resolve the packaged data dir; offenders: {offenders}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
