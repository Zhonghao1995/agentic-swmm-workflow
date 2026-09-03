"""A run folder should be named after the work, not after the user's home dir.

Two failures from one session's runs listing:

    191115_agent_chat
    191436_agent_chat
    191657_i_chat
    192025_agent_chat
    192126_agent_chat
    193233_C-Users-Hoz-AppData-Local-agenti_run

Five different Chinese questions all slugged to "agent", because the slug
regex kept ASCII alphanumerics only and a CJK prompt has none. And a pasted
Windows path slugged the whole string and truncated it, so the folder was
named after the user's home directory instead of the work in it.
"""
from __future__ import annotations

import unittest

from agentic_swmm.agent.session_bootstrap import infer_case_slug, safe_name


class NonAsciiPromptTests(unittest.TestCase):
    def test_chinese_prompts_stay_distinguishable(self) -> None:
        first = infer_case_slug("你现在是谁 你能帮我做一些什么？")
        second = infer_case_slug("你是一个什么模型？有什么能力")
        self.assertNotEqual(first, "agent")
        self.assertNotEqual(second, "agent")
        self.assertNotEqual(first, second)

    def test_mixed_scripts_survive(self) -> None:
        slug = infer_case_slug("你现在是否能链接swmmcanada 进行水文学预测？")
        self.assertIn("swmmcanada", slug)

    def test_an_empty_prompt_still_yields_a_usable_name(self) -> None:
        self.assertEqual(safe_name("   "), "agent")
        self.assertEqual(safe_name("???"), "agent")


class PastedPathTests(unittest.TestCase):
    def test_a_windows_path_is_named_by_its_last_component(self) -> None:
        slug = infer_case_slug(
            r"C:\Users\Hoz\AppData\Local\agentic-swmm-workflow\examples\33 跑一下这个目录"
        )
        self.assertEqual(slug, "33")
        self.assertNotIn("Users", slug)

    def test_a_posix_path_works_the_same_way(self) -> None:
        self.assertEqual(infer_case_slug("run /home/me/projects/mycatchment please"), "mycatchment")

    def test_a_file_path_drops_the_extension(self) -> None:
        self.assertEqual(infer_case_slug(r"open C:\data\storms\july2026.dat"), "july2026")

    def test_the_examples_rule_still_wins(self) -> None:
        # Earlier resolution steps are unchanged; the path fallback only runs
        # when nothing more specific matched.
        self.assertEqual(
            infer_case_slug("run examples/tecnopolo/tecnopolo_r1_199401.inp"), "tecnopolo"
        )

    def test_a_prompt_with_no_path_is_unaffected(self) -> None:
        self.assertEqual(infer_case_slug("give me a word report"), "word-report")


class LengthTests(unittest.TestCase):
    def test_slugs_stay_short_enough_for_a_folder_name(self) -> None:
        for prompt in ("x" * 200, "你" * 200, r"C:\a\b\{}".format("y" * 200)):
            self.assertLessEqual(len(infer_case_slug(prompt)), 32, prompt[:20])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
