"""A run started by a plain question gets a short, lower-case name (F-08).

Live finding 2026-09-02: `Which-node-flooded-the-most-and-_run` and
`Draw-the-network-map-for-that-ru_run` appeared under runs/ today.
"""

from __future__ import annotations

from agentic_swmm.agent.session_bootstrap import infer_case_slug


def test_a_question_becomes_three_content_words():
    assert infer_case_slug("Which node flooded the most and by how much?") == "node-flooded-most"


def test_an_imperative_reads_naturally():
    assert infer_case_slug("Draw the network map for that run") == "draw-network-map"


def test_places_and_files_still_win():
    assert infer_case_slug("Fetch a SWMM model from the Canada service for downtown Victoria BC") == "downtown-victoria-bc"
    assert infer_case_slug("Run examples/todcreek/model.inp") == "todcreek"


def test_a_chinese_only_prompt_keeps_a_short_safe_name():
    slug = infer_case_slug("这个模型哪个节点淹得最厉害？")
    assert slug and len(slug) <= 16 and "_" not in slug


def test_a_contraction_leaves_no_one_letter_token():
    # Live finding F-76 (2026-09-03): "It's the downtown core" -> s-downtown-core.
    assert infer_case_slug("It's the downtown core, a few blocks around Douglas Street.") == "downtown-core-few"


def test_a_fetch_request_is_named_after_the_place_not_the_verb() -> None:
    """S43 (2026-09-03): the Vancouver session was 150906_fetch-swmm-model_run."""
    from agentic_swmm.agent.session_bootstrap import infer_case_slug

    assert infer_case_slug(
        "Fetch a SWMM model from the Canada service for Vancouver, British Columbia, "
        "rainfall period November 1 to November 4 2023, then run it and audit it."
    ) == "vancouver-british-columbia"
    assert infer_case_slug(
        "Fetch a SWMM model from the Canada service for downtown Regina, Saskatchewan, "
        "rainfall period June 10 to June 13 2023, then run it and audit it."
    ).startswith("downtown-regina")


def test_a_chinese_request_is_named_after_its_first_chinese_words() -> None:
    """S44 (2026-09-03): 帮我...多伦多市中心... was named 151655_2023-11-11_run."""
    from agentic_swmm.agent.session_bootstrap import infer_case_slug

    slug = infer_case_slug(
        "帮我从 Canada 服务取一个多伦多市中心的 SWMM 模型，降雨时段 2023 年 11 月 1 日到 11 月 4 日，然后运行并审计。"
    )
    assert slug == "多伦多市中心"
    # A real Latin word in a Chinese sentence still names the run.
    assert infer_case_slug("帮我看看 swmmcanada 现在能不能连上") == "swmmcanada"
