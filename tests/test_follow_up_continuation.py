"""Follow-ups about the run in hand continue it; only new work opens a folder.

Live finding F-07 (2026-09-02, pty-driven sessions S01/S02): after a full
fetch/run/audit chain, "Draw the network map for that run." and "Which node
flooded the most, and for how long?" each opened a NEW run folder named after
the sentence (``105010_Draw-the-network-map-for-that-ru_run``), because the
shell asked ``looks_like_swmm_request`` (vocabulary: node, map, plot, ...)
whether the input was "clearly new". Every follow-up about a finished run
uses that vocabulary. With a turn or run already in hand the dispatch now
asks a narrower question: does this sentence START new modelling work?
"""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import pytest

from agentic_swmm.agent.intent_classifier import looks_like_new_modeling_request

CONTINUES = [
    "What were the three busiest conduits by peak flow in that run?",
    "Draw the network map for that run.",
    "Which node flooded the most, and for how long?",
    "Compare this run with the previous downtown Victoria run and tell me if they match.",
    "Plot the hydrograph at the main outfall.",
    "Export a Word report for it.",
    "Audit it again against the design rulebook.",
    "Run the model again with the recommended defaults.",
    "use the recommended defaults",
    "[-123.425, 48.428, -123.413, 48.437]",
    "How long did the simulation take?",
    "Calibrate that run against the observed flow at examples/calibration/observed_flow.csv",
    "Run climate scenarios on that model with precipitation factors 1.0, 1.2 and 1.5",
    "这个模型里哪个节点淹水最严重？",
    "把刚才那个运行的管网图画出来",
]

STARTS_NEW_WORK = [
    "Fetch a SWMM model from the Canada service for the Esquimalt area of Victoria BC, "
    "rainfall period November 1 to November 4 2023.",
    "Run climate scenarios on examples/tecnopolo/tecnopolo_r1_199401.inp with precipitation factors 1.0, 1.2 and 1.5",
    "Calibrate the manning_n parameter of the model at examples/todcreek/model_chicago5min.inp against observed flow",
    "Run the model at examples/todcreek/model_chicago5min.inp",
    "Now do the same for James Bay, bbox [-123.383, 48.414, -123.371, 48.423]",
    "给我温哥华的真实管网模型",
    "Get me a model for downtown Toronto, Canada",
    "Build a model for the area around Ottawa city hall",
    "Synthesize a network for this bbox: [-123.42, 48.43, -123.41, 48.44]",
]


class TestClassifier:
    @pytest.mark.parametrize("prompt", CONTINUES)
    def test_follow_ups_continue(self, prompt):
        assert looks_like_new_modeling_request(prompt) is False

    @pytest.mark.parametrize("prompt", STARTS_NEW_WORK)
    def test_new_work_opens_a_folder(self, prompt):
        assert looks_like_new_modeling_request(prompt) is True

    @pytest.mark.parametrize(
        "reply",
        [
            "bbox [-123.425, 48.428, -123.413, 48.437]",
            "[-123.425, 48.428, -123.413, 48.437]",
            "yes, run the model",
            "use examples/todcreek/model_chicago5min.inp",
        ],
    )
    def test_an_answer_to_the_assistant_question_continues(self, reply):
        # The previous turn ended with a question; a short reply that names
        # a source is the ANSWER to it, not a new job.
        assert looks_like_new_modeling_request(reply, answering_question=True) is False

    def test_new_work_still_wins_over_an_open_question(self):
        assert looks_like_new_modeling_request(
            "Fetch a model for the Esquimalt area instead", answering_question=True
        ) is True


# ---------------------------------------------------------------------------
# Shell level: the same three-turn conversation the live sessions ran.
# ---------------------------------------------------------------------------


def _run_shell(feed_lines: list[str], outcomes: list[str], *, mark_run_dir: bool = True) -> list[dict]:
    """Drive ``run_interactive_shell`` with a fake planner; return its calls."""
    from agentic_swmm.agent import runtime_loop

    calls: list[dict] = []
    outcome_iter = iter(outcomes)

    def fake_planner(args, goal, session_dir, trace_path, registry,
                     *, chat_session=False, prior_session_state=None,
                     outcome_box=None):
        calls.append({"goal": goal, "session_dir": Path(session_dir), "chat": chat_session})
        if mark_run_dir:
            # What a real turn leaves behind: a manifest and a runner stage,
            # which is how the shell recognises an active SWMM run dir.
            (Path(session_dir) / "manifest.json").write_text("{}", encoding="utf-8")
            (Path(session_dir) / "06_runner").mkdir(exist_ok=True)
        if outcome_box is not None:
            outcome_box.append(next(outcome_iter, "Done."))
        return 0

    feed = iter([*feed_lines, "/exit"])
    with TemporaryDirectory() as tmp:
        args = Namespace(
            planner="llm", provider=None, model=None,
            session_dir=Path(tmp), max_steps=4, verbose=False,
            dry_run=False, safe=False, interactive=True,
        )
        with mock.patch.dict(os.environ, {"AISWMM_DISABLE_WELCOME": "1"}):
            with mock.patch.object(runtime_loop, "run_openai_planner", fake_planner), mock.patch(
                "builtins.input", lambda _p="": next(feed)
            ):
                runtime_loop.run_interactive_shell(args)
    return calls


CHAIN = (
    "Fetch a SWMM model from the Canada service for downtown Victoria BC, rainfall period "
    "November 1 to November 4 2023. Run the model, audit it, and export a Word report."
)


class TestShellDispatch:
    def test_follow_ups_stay_in_the_chain_run_dir(self):
        calls = _run_shell(
            [CHAIN, "Draw the network map for that run.", "Which node flooded the most, and for how long?"],
            ["Done. Peak 0.116 CMS at OUT_DMH002395.", "Map written.", "DMH001395 flooded for 9.16 h."],
        )
        assert len(calls) == 3
        assert calls[1]["session_dir"] == calls[0]["session_dir"]
        assert calls[2]["session_dir"] == calls[0]["session_dir"]
        assert "Previous run directory" in calls[2]["goal"]
        assert not calls[1]["chat"] and not calls[2]["chat"]

    def test_a_new_fetch_after_the_chain_opens_a_fresh_run_dir(self):
        calls = _run_shell(
            [
                CHAIN,
                "Fetch a SWMM model from the Canada service for the James Bay area of Victoria BC, "
                "bbox [-123.383, 48.414, -123.371, 48.423], rainfall period November 1 to November 4 2023.",
            ],
            ["Done.", "Done."],
        )
        assert len(calls) == 2
        assert calls[1]["session_dir"] != calls[0]["session_dir"]
        assert "james-bay" in calls[1]["session_dir"].name

    def test_a_bbox_reply_answers_the_pending_question_in_place(self):
        calls = _run_shell(
            [
                "Fetch a SWMM model from the Canada service for the Esquimalt area of Victoria BC, "
                "rainfall period November 1 to November 4 2023. Run the model and audit it.",
                "bbox [-123.425, 48.428, -123.413, 48.437]",
            ],
            ["I need the area. Which bbox should I use, as [west, south, east, north]?", "Done."],
            mark_run_dir=False,
        )
        assert len(calls) == 2
        assert calls[1]["session_dir"] == calls[0]["session_dir"]
        assert "Which bbox should I use" in calls[1]["goal"]

    def test_run_folder_name_still_comes_from_the_place(self):
        calls = _run_shell([CHAIN], ["Done."])
        assert "downtown-victoria" in calls[0]["session_dir"].name
