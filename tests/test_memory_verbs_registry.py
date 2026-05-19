"""Tests for the memory verb registry (PRD-06 Phase D.1).

The registry is the single source of truth for the stakes hint the
planner uses to decide which confidence quadrant a goal lands in.
These tests pin:

1. Default mode lists exactly the four user-facing verbs.
2. Expert mode lists every default verb plus the four expert-only
   verbs.
3. The stakes hint propagates from registry → planner's
   ``_looks_high_stakes`` → ``decide_with_memory``.
4. The registry never raises on unknown verbs.
"""

from __future__ import annotations

import unittest
from unittest import mock

from agentic_swmm.agent import memory_verbs


class MemoryVerbsRegistryTests(unittest.TestCase):
    """Pin the registry shape the planner depends on."""

    def test_default_mode_lists_four_user_verbs(self) -> None:
        names = {v.name for v in memory_verbs.list_verbs(mode="default")}
        self.assertEqual(
            names,
            {"compare", "cite", "storm", "transfer"},
            "Default mode must list compare/cite/storm/transfer verbatim — "
            "any other set means the registry drifted.",
        )

    def test_expert_mode_is_additive(self) -> None:
        default_names = {v.name for v in memory_verbs.list_verbs(mode="default")}
        expert_names = {v.name for v in memory_verbs.list_verbs(mode="expert")}
        self.assertTrue(default_names.issubset(expert_names))
        # Four expert-only verbs added on top of the four default verbs.
        added = expert_names - default_names
        self.assertEqual(
            added,
            {
                "uncertainty.plan",
                "calibration-memory.read",
                "negative-lessons.read",
                "case-adaptive-thresholds",
            },
        )

    def test_default_verbs_have_low_stakes(self) -> None:
        for verb in memory_verbs.list_verbs(mode="default"):
            self.assertEqual(
                verb.stakes,
                "low",
                f"default verb {verb.name!r} must be low-stakes; "
                f"got {verb.stakes!r}",
            )

    def test_calibration_verbs_are_high_stakes(self) -> None:
        for name in (
            "calibration-memory.read",
            "negative-lessons.read",
            "case-adaptive-thresholds",
        ):
            self.assertEqual(memory_verbs.stakes_for(name), "high")

    def test_uncertainty_plan_is_low_stakes(self) -> None:
        # ``uncertainty plan`` is read-only / advisory — it only
        # computes a sample list. It is expert-mode because it is a
        # niche verb, not because it is dangerous.
        self.assertEqual(memory_verbs.stakes_for("uncertainty.plan"), "low")

    def test_unknown_verb_returns_none(self) -> None:
        self.assertIsNone(memory_verbs.get_verb("does-not-exist"))
        self.assertIsNone(memory_verbs.stakes_for("does-not-exist"))

    def test_register_replaces_existing_row(self) -> None:
        # Pin the replacement semantics so a test fixture can override
        # a verb's stakes without polluting other tests.
        with mock.patch.dict(memory_verbs._REGISTRY, dict(memory_verbs._REGISTRY)):
            memory_verbs.register(
                memory_verbs.MemoryVerb(
                    name="compare",
                    description="(test override)",
                    cli_path="aiswmm compare",
                    mode="default",
                    stakes="high",
                )
            )
            self.assertEqual(memory_verbs.stakes_for("compare"), "high")

    def test_list_verbs_is_sorted(self) -> None:
        names = [v.name for v in memory_verbs.list_verbs(mode="expert")]
        self.assertEqual(names, sorted(names))

    def test_every_verb_has_required_fields(self) -> None:
        for verb in memory_verbs.list_verbs(mode="expert"):
            self.assertTrue(verb.name, "name must be non-empty")
            self.assertTrue(verb.description, "description must be non-empty")
            self.assertTrue(
                verb.cli_path.startswith("aiswmm "),
                f"cli_path must start with 'aiswmm '; got {verb.cli_path!r}",
            )
            self.assertIn(verb.mode, ("default", "expert"))
            self.assertIn(verb.stakes, ("low", "high"))


class StakesHintPropagationTests(unittest.TestCase):
    """The registry's stakes hint must flow into the planner's stakes sniff."""

    def test_high_stakes_verb_in_goal_is_recognised(self) -> None:
        from agentic_swmm.agent.planner import _looks_high_stakes

        # A goal mentioning a registered high-stakes verb fires
        # high-stakes regardless of the legacy keyword sniff.
        self.assertTrue(
            _looks_high_stakes(
                "please run case-adaptive-thresholds on saanich-b8"
            )
        )

    def test_low_stakes_verb_in_goal_is_low_stakes(self) -> None:
        from agentic_swmm.agent.planner import _looks_high_stakes

        # ``compare`` is low-stakes; a goal mentioning it stays low.
        self.assertFalse(_looks_high_stakes("compare runs A and B"))

    def test_legacy_accept_calibration_still_fires(self) -> None:
        # The existing keyword sniff for accept-calibration must keep
        # working — we extended it, not replaced it.
        from agentic_swmm.agent.planner import _looks_high_stakes

        self.assertTrue(_looks_high_stakes("accept calibration for saanich"))


if __name__ == "__main__":
    unittest.main()
