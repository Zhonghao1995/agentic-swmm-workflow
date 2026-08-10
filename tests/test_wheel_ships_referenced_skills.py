"""Every skill family the runtime references must ship in the public wheel.

setup.py builds the wheel's data files from an explicit PUBLIC_SKILLS
allowlist. When a tool handler grows a resource_path("skills", ...)
reference that the allowlist lacks, the git-clone install keeps working
while a pip-installed wheel breaks that verb, which is invisible until
someone runs the wheel. That is exactly how swmm-report (the Word
deliverable) was missing from the published wheel for two releases,
caught by the v0.9.0 release smoke.

This test pins: skills referenced from agentic_swmm source ⊆ PUBLIC_SKILLS.
The allowlist may ship MORE than the referenced set (skills used through
their SKILL.md contract rather than a hardcoded path); it may never ship
less.
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "setup.py"
PACKAGE = REPO / "agentic_swmm"

# Matches both reference styles used in the package:
#   resource_path("skills", "swmm-report", ...)   tuple-style
#   "skills/swmm-plot/scripts/..."                path-string style
_TUPLE_REF = re.compile(r'"skills",\s*"([a-z0-9-]+)"')
_PATH_REF = re.compile(r'skills/([a-z0-9-]+)/')


def _public_skills_from_setup() -> set[str]:
    """Read the PUBLIC_SKILLS literal without importing setup.py.

    Importing would execute setup() at module bottom; parsing the AST
    keeps this test side-effect free.
    """
    tree = ast.parse(SETUP.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PUBLIC_SKILLS":
                    value = ast.literal_eval(node.value)
                    return set(value)
    raise AssertionError("PUBLIC_SKILLS not found in setup.py")


def _referenced_skills() -> set[str]:
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(errors="ignore")
        found.update(_TUPLE_REF.findall(text))
        found.update(_PATH_REF.findall(text))
    return found


class WheelShipsReferencedSkillsTests(unittest.TestCase):
    def test_every_runtime_referenced_skill_is_in_the_wheel_allowlist(self) -> None:
        allowlist = _public_skills_from_setup()
        referenced = _referenced_skills()
        self.assertTrue(referenced, "reference scan found nothing; regexes stale?")
        missing = sorted(referenced - allowlist)
        self.assertEqual(
            missing,
            [],
            "skills referenced by agentic_swmm but absent from setup.py "
            f"PUBLIC_SKILLS (pip installs would break these verbs): {missing}",
        )

    def test_allowlisted_skills_exist_in_the_repo(self) -> None:
        for name in sorted(_public_skills_from_setup()):
            self.assertTrue(
                (REPO / "skills" / name).is_dir(),
                f"PUBLIC_SKILLS lists {name} but skills/{name}/ does not exist",
            )


if __name__ == "__main__":
    unittest.main()
