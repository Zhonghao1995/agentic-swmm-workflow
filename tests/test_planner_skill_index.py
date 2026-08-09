"""Guard tests: the inline skill index (ADR token-economy decision 1).

Every NL session used to open with a catalog prologue (list_skills ->
read_skill x5-7 -> list_mcp_servers -> list_mcp_tools x4) whose
payloads then compounded through full-history replay (measured
2026-08-09: 12 of 26 calls in a map session; a canada session spent
ALL its steps in catalogs). The system prompt now embeds a one-line-
per-skill index and instructs the planner to commit via select_skill
directly instead of reconnoitering.

Pinned here:
* the index block stays under its size budget (so skill descriptions
  cannot silently bloat the system prompt),
* every enabled skill appears in the index,
* the assembled prompt carries the index and the no-reconnaissance
  instruction, and no longer tells the planner to START with
  list_skills.
"""

from __future__ import annotations

import unittest

from agentic_swmm.agent.prompts import (
    _SKILL_INDEX_BUDGET,
    openai_planner_prompt,
    skill_index_block,
)
from agentic_swmm.runtime.registry import discover_skills


class SkillIndexBlockTests(unittest.TestCase):
    def test_block_stays_under_budget(self) -> None:
        self.assertLessEqual(len(skill_index_block()), _SKILL_INDEX_BUDGET)

    def test_every_enabled_skill_listed(self) -> None:
        block = skill_index_block()
        for record in discover_skills():
            if record.get("enabled", True):
                self.assertIn(f"- {record['name']}", block)

    def test_block_is_fenced(self) -> None:
        block = skill_index_block()
        self.assertTrue(block.startswith("<skill-index>"))
        self.assertTrue(block.endswith("</skill-index>"))


class PromptIntegrationTests(unittest.TestCase):
    def test_prompt_embeds_index_and_no_recon_instruction(self) -> None:
        prompt = openai_planner_prompt()
        self.assertIn("<skill-index>", prompt)
        self.assertIn("catalog reconnaissance", prompt)
        self.assertIn("call select_skill DIRECTLY", prompt)

    def test_prompt_no_longer_opens_with_list_skills(self) -> None:
        prompt = openai_planner_prompt()
        self.assertNotIn("Start by listing skills with list_skills", prompt)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class OversizeDegradeTests(unittest.TestCase):
    def test_oversized_index_degrades_to_names_only(self) -> None:
        """The fail-soft path: an index over budget drops descriptions
        instead of blowing the prompt. Exercised by shrinking the
        budget below the real block's size."""
        from unittest import mock

        from agentic_swmm.agent import prompts as prompts_mod

        prompts_mod.skill_index_block.cache_clear()
        try:
            with mock.patch.object(prompts_mod, "_SKILL_INDEX_BUDGET", 200):
                block = prompts_mod.skill_index_block()
        finally:
            prompts_mod.skill_index_block.cache_clear()
        self.assertTrue(block.startswith("<skill-index>"))
        self.assertTrue(block.endswith("</skill-index>"))
        # Names-only: no description separator remains on skill lines.
        body_lines = [
            line for line in block.splitlines()
            if line.startswith("- ")
        ]
        self.assertTrue(body_lines)
        for line in body_lines:
            self.assertNotIn(": ", line)
