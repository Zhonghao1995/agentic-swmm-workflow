"""Regression tests: interactive-hardening batch 1 (user live test spec).

Source: the user's own terminal test (docs bug record, 2026-08-09).
Covered here:

* BUG-2 classifier half: data-shaped replies (bare bbox, digits,
  paths) and short CJK task sentences are not "open shaped", so they
  can never be greeted by the warm intro.
* Warm intro fires at most on the FIRST prompt of a session (repl
  gate), so mid-conversation answers are never hijacked.
* One confirmation per turn (QUICK profile): the first approved Y
  arms the rest of the turn's prompted tools; a denial does not arm;
  SAFE keeps prompting every time.
* Location-based session slugs: goals naming a place produce
  place-named dirs instead of "plot-selection".
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from agentic_swmm.agent import repl as repl_mod
from agentic_swmm.agent.executor import AgentExecutor
from agentic_swmm.agent.intent_classifier import classify_intent
from agentic_swmm.agent.permissions_profile import Profile
from agentic_swmm.agent.session_bootstrap import infer_case_slug
from agentic_swmm.agent.types import ToolCall, ToolSpec


class OpenShapedClassifierTests(unittest.TestCase):
    def test_bare_bbox_is_not_open_shaped(self) -> None:
        signals = classify_intent("[-123.370, 48.425, -123.360, 48.432]")
        self.assertFalse(signals.is_open_shaped)

    def test_short_cjk_task_sentence_is_not_open_shaped(self) -> None:
        signals = classify_intent("用推荐默认值画降雨-径流图")
        self.assertFalse(signals.is_open_shaped)

    def test_greeting_stays_open_shaped(self) -> None:
        self.assertTrue(classify_intent("hello").is_open_shaped)
        self.assertTrue(classify_intent("你好").is_open_shaped)


class WarmIntroFirstTurnOnlyTests(unittest.TestCase):
    def _run_repl(self, prompts: list[str]) -> list[str]:
        outputs: list[str] = []
        feed = iter(prompts + ["/exit"])

        def input_source(_prompt: str) -> str:
            return next(feed)

        def planner_runner(args, prompt, session_dir, trace_path, registry, **kw):
            outputs.append(f"PLANNED:{prompt}")
            return 0

        repl_mod.run_repl(
            args=mock.Mock(),
            base_dir=Path("."),
            profile_name="quick",
            input_source=input_source,
            planner_runner=planner_runner,
            output=outputs.append,
        )
        return outputs

    def test_second_short_reply_is_planned_not_greeted(self) -> None:
        """BUG-2: the bare-bbox second input used to get the warm
        intro; now only the first prompt is intro-eligible."""
        outputs = self._run_repl(
            [
                "Fetch a model for downtown Victoria BC and run it",
                "[-123.370, 48.425, -123.360, 48.432]",
            ]
        )
        planned = [o for o in outputs if o.startswith("PLANNED:")]
        self.assertEqual(len(planned), 2)
        self.assertFalse(any("stormwater modeling collaborator" in o for o in outputs))

    def test_first_greeting_still_gets_the_intro(self) -> None:
        outputs = self._run_repl(["hello", "run the tecnopolo demo"])
        self.assertTrue(any("stormwater modeling collaborator" in o for o in outputs))


class _TinyRegistry:
    """Two consequential tools + one read-only, no side effects."""

    def __init__(self) -> None:
        self.specs = {
            "fetch": ToolSpec("fetch", "fetch", {"type": "object"}, lambda c, s: {"ok": True, "summary": "fetched"}),
            "run": ToolSpec("run", "run", {"type": "object"}, lambda c, s: {"ok": True, "summary": "ran"}),
        }

    def is_read_only(self, name: str) -> bool:
        return False

    def execute(self, call: ToolCall, session_dir: Path):
        return dict(self.specs[call.name].handler(call, session_dir))


class ChainApprovalTests(unittest.TestCase):
    def _executor(self, profile: Profile, tmp: Path) -> AgentExecutor:
        return AgentExecutor(
            _TinyRegistry(),
            session_dir=tmp,
            trace_path=tmp / "trace.jsonl",
            dry_run=False,
            profile=profile,
        )

    def test_quick_first_yes_approves_rest_of_turn(self) -> None:
        with TemporaryDirectory() as tmp:
            ex = self._executor(Profile.QUICK, Path(tmp))
            with mock.patch(
                "agentic_swmm.agent.executor.permissions.request_approval"
            ) as approval:
                approval.return_value = mock.Mock(approved=True, needs_guidance=False)
                r1 = ex.execute(ToolCall(name="fetch", args={}))
                r2 = ex.execute(ToolCall(name="run", args={}))
            self.assertEqual(approval.call_count, 1)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertTrue(r1["permission"]["prompted"])
        self.assertFalse(r2["permission"]["prompted"])

    def test_quick_denial_does_not_arm_the_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            ex = self._executor(Profile.QUICK, Path(tmp))
            with mock.patch(
                "agentic_swmm.agent.executor.permissions.request_approval"
            ) as approval:
                approval.side_effect = [
                    mock.Mock(approved=False, needs_guidance=False),
                    mock.Mock(approved=True, needs_guidance=False),
                ]
                r1 = ex.execute(ToolCall(name="fetch", args={}))
                r2 = ex.execute(ToolCall(name="run", args={}))
            self.assertEqual(approval.call_count, 2)
        self.assertFalse(r1["ok"])
        self.assertTrue(r2["ok"])

    def test_safe_profile_prompts_every_time(self) -> None:
        with TemporaryDirectory() as tmp:
            ex = self._executor(Profile.SAFE, Path(tmp))
            with mock.patch(
                "agentic_swmm.agent.executor.permissions.request_approval"
            ) as approval:
                approval.return_value = mock.Mock(approved=True, needs_guidance=False)
                ex.execute(ToolCall(name="fetch", args={}))
                ex.execute(ToolCall(name="run", args={}))
            self.assertEqual(approval.call_count, 2)


class PlaceSlugTests(unittest.TestCase):
    def test_parenthetical_place_wins_over_plot_vocab(self) -> None:
        slug = infer_case_slug(
            "帮我从 SWMMCanada 取维多利亚市中心(downtown Victoria, BC)的模型,画降雨-径流图,生成 Word 报告"
        )
        self.assertEqual(slug, "downtown-victoria-bc")

    def test_inline_place_with_suffix_anchor(self) -> None:
        slug = infer_case_slug(
            "Fetch a SWMM model for the James Bay area of Victoria BC and plot the hydrograph"
        )
        self.assertTrue(slug.startswith("james-bay"))

    def test_sentence_initial_verb_is_not_a_place(self) -> None:
        slug = infer_case_slug("Design a 2-year Chicago storm")
        self.assertNotEqual(slug, "design")

    def test_plot_vocab_fallback_survives_without_place(self) -> None:
        self.assertEqual(infer_case_slug("plot the outfall hydrograph"), "plot-selection")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
