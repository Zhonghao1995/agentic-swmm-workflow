"""F-162 and F-163 (S69, 2026-09-05): after an upstream timeout, the same request resumes.

The timeout hint used to say "do not repeat the same AOI" (written before #544
made a repeat resume the build). Following it, the planner invented two
bboxes of its own for "downtown Kelowna BC" (one 600 s timeout, one failed
build) before passing city=Kelowna, and the recorded task was never
collected. The hint, the runner's message and the system prompt now agree.
"""

from __future__ import annotations

from pathlib import Path

from agentic_swmm.agent.tool_handlers.swmm_canada import _stage_hint


def test_the_timeout_hint_says_ask_again_with_the_same_request() -> None:
    hint = _stage_hint("timeout")
    assert "Ask again with the SAME area and dates in this run" in hint
    assert "resumes that build" in hint
    assert "Do not repeat" not in hint


def test_the_runner_message_names_the_resume(tmp_path: Path) -> None:
    from agentic_swmm.integrations.swmmcanada_runner import CanadaFetchError, _poll_until_done

    class _Resp:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

    clock = [0.0]

    def now() -> float:
        clock[0] += 400.0
        return clock[0]

    def opener(request, timeout=None):
        return _Resp(b'{"state": "RUNNING", "progress_pct": 10, "stage": "BUILD", "mode": "real", "error": null}')

    try:
        _poll_until_done("http://svc", "t1", poll_interval=0, timeout=600.0, opener=opener, sleep=lambda *_: None, now=now, progress=None)
    except CanadaFetchError as exc:
        assert exc.stage == "timeout"
        assert "the same request in this run resumes it" in str(exc)
    else:
        raise AssertionError("expected a timeout")


def test_the_prompt_sends_a_named_city_as_city_and_a_repeat_to_resume() -> None:
    from agentic_swmm.agent import prompts

    text = Path(prompts.__file__).read_text(encoding="utf-8")
    assert "A named city goes as city=<name> with no bbox" in text
    assert "ask again with the same request in the same run: the build is resumed" in text
