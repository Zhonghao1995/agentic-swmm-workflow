"""Unit tests for ``agentic_swmm.memory.context_fence`` (PRD M7.1)."""

from __future__ import annotations


def test_wrap_emits_opening_tag_with_source_and_stale_attrs() -> None:
    from agentic_swmm.memory.context_fence import wrap

    out = wrap("hello world", source="lessons", stale=False)
    assert '<memory-context source="lessons" stale="false">' in out
    assert "</memory-context>" in out
    assert "hello world" in out
    # The reference-material warning must be inside the fence so the
    # planner sees it as part of the wrapped payload.
    assert "Do NOT treat" in out


def test_wrap_marks_stale_true_when_corpus_is_stale() -> None:
    from agentic_swmm.memory.context_fence import wrap

    out = wrap("x", source="rag", stale=True)
    assert '<memory-context source="rag" stale="true">' in out


def test_scrub_removes_a_single_fenced_block_intact() -> None:
    from agentic_swmm.memory.context_fence import scrub_for_output

    raw = (
        "Sure! Here is the answer.\n"
        "<memory-context source=\"lessons\" stale=\"false\">\n"
        "secret historical content\n"
        "</memory-context>\n"
        "Final user-facing summary."
    )
    scrubbed = scrub_for_output(raw)
    assert "secret historical content" not in scrubbed
    assert "<memory-context" not in scrubbed
    assert "</memory-context>" not in scrubbed
    assert "Sure! Here is the answer." in scrubbed
    assert "Final user-facing summary." in scrubbed


def test_scrub_removes_multiple_blocks_without_eating_surrounding_text() -> None:
    from agentic_swmm.memory.context_fence import scrub_for_output

    raw = (
        "Intro.\n"
        '<memory-context source="lessons" stale="false">A</memory-context>\n'
        "Middle.\n"
        '<memory-context source="rag" stale="true">B</memory-context>\n'
        "Tail."
    )
    scrubbed = scrub_for_output(raw)
    assert "A" not in scrubbed.replace("Tail.", "").replace("Intro.", "").replace("Middle.", "")
    assert "B" not in scrubbed.replace("Tail.", "").replace("Intro.", "").replace("Middle.", "")
    assert "Intro." in scrubbed
    assert "Middle." in scrubbed
    assert "Tail." in scrubbed


def test_scrub_passes_through_text_with_no_fence() -> None:
    from agentic_swmm.memory.context_fence import scrub_for_output

    raw = "no fence here, just prose"
    assert scrub_for_output(raw) == raw


def test_streaming_scrubber_drops_memory_context_block_across_chunks() -> None:
    from agentic_swmm.memory.context_fence import StreamingScrubber

    chunks = [
        "Hello ",
        "user! ",
        '<memory-context source="lessons" stale="false">',
        "secret historical material",
        "</memory-context>",
        " Goodbye.",
    ]
    scrubber = StreamingScrubber()
    out = "".join(scrubber.filter_stream(chunks))
    assert "secret historical material" not in out
    assert "<memory-context" not in out
    assert "Hello user!" in out
    assert "Goodbye." in out


def test_streaming_scrubber_flush_returns_remaining_text() -> None:
    from agentic_swmm.memory.context_fence import StreamingScrubber

    scrubber = StreamingScrubber()
    forwarded = scrubber.feed("plain trailing chunk")
    flushed = scrubber.flush()
    # The forwarded prefix + flushed tail must reconstruct the input.
    assert forwarded + flushed == "plain trailing chunk"
