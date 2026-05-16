"""Skill registry discovery (issue #113).

Every skill that has an on-disk ``SKILL.md`` under ``skills/<name>/``
must appear in the planner-visible skill list returned by
``SkillRouter.list_skills()``. Otherwise the planner refuses to
``select_skill`` it ("unknown skill: ..."), even though the skill is
present in the repository.

The original symptom was ``swmm-end-to-end`` (the orchestration
contract): the file existed at ``skills/swmm-end-to-end/SKILL.md`` but
the router omitted it from the known list, so the planner could not
commit to that skill at all.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.skill_router import SkillRouter
from agentic_swmm.agent.tool_registry import AgentToolRegistry
from agentic_swmm.runtime.registry import discover_skills


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_swmm_end_to_end_is_a_known_skill() -> None:
    router = SkillRouter(AgentToolRegistry())
    skills = router.list_skills()
    assert "swmm-end-to-end" in skills, (
        f"swmm-end-to-end must be a known skill; got {skills}. "
        f"Otherwise select_skill('swmm-end-to-end') fails with "
        f"'unknown skill' even though skills/swmm-end-to-end/SKILL.md "
        f"is on disk."
    )


def test_every_on_disk_skill_is_known_to_the_router() -> None:
    """Regression guard for the whole skills/ directory.

    Walks every ``skills/*/SKILL.md`` from the filesystem and asserts
    each name appears in the router's reported skill list. Acts as a
    comprehensive guard for any future skill name that gets dropped.
    """

    on_disk = {record["name"] for record in discover_skills()}
    assert on_disk, "expected at least one skill under skills/"

    router = SkillRouter(AgentToolRegistry())
    known = set(router.list_skills())

    missing = on_disk - known
    assert not missing, (
        f"on-disk skills missing from router.list_skills(): {sorted(missing)}. "
        f"Every skills/<name>/SKILL.md must surface in the planner-visible "
        f"skill set."
    )
