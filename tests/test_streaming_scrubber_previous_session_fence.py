"""StreamingScrubber strips ``<previous-session>`` from final output.

The session-db-facts PRD adds a startup-injected ``<previous-session>``
banner (and a ``<project-facts>`` injection) to the system prompt. The
scrubber must treat those fences identically to ``<memory-context>``
so the model cannot accidentally echo them in the final user-visible
reply.
"""

from __future__ import annotations


def test_scrub_for_output_strips_previous_session_block() -> None:
    from agentic_swmm.memory.context_fence import scrub_for_output

    raw = (
        "Sure, here's the answer.\n"
        '<previous-session case="tecnopolo" session_id="20260513_x" ok=false ended="2026-05-13T20:00:00+00:00">\n'
        "goal: plot the figure for the result\n"
        "</previous-session>\n"
        "Now back to the user-visible reply."
    )
    out = scrub_for_output(raw)
    assert "<previous-session" not in out
    assert "</previous-session>" not in out
    assert "plot the figure for the result" not in out
    assert "Sure, here's the answer." in out
    assert "Now back to the user-visible reply." in out


def test_streaming_scrubber_drops_previous_session_across_chunks() -> None:
    from agentic_swmm.memory.context_fence import StreamingScrubber

    chunks = [
        "Hello user. ",
        '<previous-session case="todcreek" session_id="abc" ok=true ended="now">',
        "\ngoal: yesterday we tuned the soil model\n",
        "</previous-session>",
        " Goodbye.",
    ]
    scrubber = StreamingScrubber()
    out = "".join(scrubber.filter_stream(chunks))
    assert "<previous-session" not in out
    assert "tuned the soil model" not in out
    assert "Hello user." in out
    assert "Goodbye." in out


def test_scrub_strips_project_facts_block_too() -> None:
    from agentic_swmm.memory.context_fence import scrub_for_output

    raw = (
        "Intro.\n"
        '<project-facts source="curated">\n'
        "- user prefers metric units\n"
        "</project-facts>\n"
        "Tail."
    )
    out = scrub_for_output(raw)
    assert "<project-facts" not in out
    assert "user prefers metric units" not in out
    assert "Intro." in out
    assert "Tail." in out
