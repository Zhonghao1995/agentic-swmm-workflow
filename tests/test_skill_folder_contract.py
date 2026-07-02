"""Suite-level lock on the skill folder contract.

``skills/skill-author/scripts/validate_skill.py`` defines the structural
contract every skill must satisfy (frontmatter with a kebab-case ``name``
matching the folder, a real ``description``, a non-empty body). It used to
run only when skill-author drafts a NEW proposal; this suite runs it
against every shipped skill so contract drift is caught by the test suite
instead of at the next skill-authoring session.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).parent.parent
_VALIDATOR = _REPO / "skills" / "skill-author" / "scripts" / "validate_skill.py"
_SKILLS_DIR = _REPO / "skills"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_skill_suite", _VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_validate = _load_validator().validate

_ALL_SKILLS = sorted(
    p.name for p in _SKILLS_DIR.iterdir() if p.is_dir() and not p.name.startswith(".")
)


@pytest.mark.parametrize("skill_name", _ALL_SKILLS)
def test_skill_folder_is_well_formed(skill_name: str) -> None:
    problems = _validate(_SKILLS_DIR / skill_name)
    assert problems == [], f"{skill_name}: {problems}"


def test_sweep_covers_the_shipped_skills() -> None:
    """Guard against the parametrize list silently going empty."""
    assert len(_ALL_SKILLS) >= 19