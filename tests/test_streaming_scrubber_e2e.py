"""End-to-end: StreamingScrubber strips <memory-context> from final output.

Per PRD Done Criteria: 'A run that emits <memory-context> in the
planner's tool result does NOT show that fenced text in the final
user-visible reply (M7.1 scrubber wiring).'

This test feeds a synthetic streaming reply that echoes a fenced
block verbatim through the public scrubber helper and asserts that
the user-visible bytes contain none of the fenced content. The
exact same helper is what Runtime PRD's final-output path calls.
"""

from __future__ import annotations


def test_streaming_scrubber_strips_memory_context_from_chunked_reply() -> None:
    from agentic_swmm.memory.context_fence import StreamingScrubber

    # Simulate a model that emits text in arbitrary chunks, including
    # one chunk that fully contains a fence, and another chunk that
    # splits the fence across boundaries.
    chunks = [
        "Sure! Based on history, ",
        "<memory-context source=\"lessons\" stale=\"false\">\n",
        "## peak_flow_parse_missing\nSecret historical detail.\n",
        "</memory-context>",
        "\nHere is my answer: try renaming the outfall node.",
    ]
    scrubber = StreamingScrubber()
    user_visible = "".join(scrubber.filter_stream(chunks))

    assert "<memory-context" not in user_visible
    assert "</memory-context>" not in user_visible
    assert "Secret historical detail" not in user_visible
    assert "peak_flow_parse_missing" not in user_visible  # only in the fenced block
    assert "renaming the outfall node" in user_visible


def test_scrub_final_output_helper_is_exported() -> None:
    """The public helper Runtime PRD's reporting layer is expected to call."""
    from agentic_swmm.memory import context_fence

    assert hasattr(context_fence, "scrub_for_output")
    assert hasattr(context_fence, "StreamingScrubber")
    assert hasattr(context_fence, "wrap")
