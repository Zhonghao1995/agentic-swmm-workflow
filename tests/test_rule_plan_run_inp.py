"""The rule planner routes an explicit run-<inp> goal to run_swmm_inp.

The rule planner is deliberately minimal (the LLM planner owns fuzzy
goals), but "run <model>.inp" is unambiguous and used to fall through
to the doctor fallback: the default `aiswmm agent "run x.inp"` did a
setup check instead of running the model.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.planner import rule_plan


class RunInpRuleTests(unittest.TestCase):
    def test_run_plus_inp_path_routes_to_run_swmm_inp(self) -> None:
        plan = rule_plan("run examples/todcreek/model_chicago5min.inp")
        self.assertEqual(plan[0].name, "run_swmm_inp")
        self.assertEqual(
            plan[0].args["inp_path"], "examples/todcreek/model_chicago5min.inp"
        )

    def test_bare_filename_resolves_under_examples(self) -> None:
        plan = rule_plan("please run model_chicago5min.inp")
        self.assertEqual(plan[0].name, "run_swmm_inp")
        self.assertTrue(plan[0].args["inp_path"].endswith("examples/todcreek/model_chicago5min.inp"))

    def test_chinese_run_verb_also_routes(self) -> None:
        plan = rule_plan("跑一下 examples/todcreek/model_chicago5min.inp")
        self.assertEqual(plan[0].name, "run_swmm_inp")

    def test_inp_mention_without_run_intent_keeps_doctor_fallback(self) -> None:
        plan = rule_plan("what is examples/todcreek/model_chicago5min.inp")
        self.assertEqual([c.name for c in plan], ["doctor"])

    def test_no_inp_still_falls_back_to_doctor(self) -> None:
        plan = rule_plan("hello world")
        self.assertEqual([c.name for c in plan], ["doctor"])


if __name__ == "__main__":
    unittest.main()
