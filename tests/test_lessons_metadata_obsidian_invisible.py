"""Obsidian preview must hide the metadata block.

The fence is a single multi-line HTML comment whose first line carries
``<!-- aiswmm-metadata`` and whose last line ends with
``/aiswmm-metadata -->``. CommonMark / Obsidian preview treats the
whole comment (including the YAML payload inside) as a hidden HTML
block, so none of the metadata tokens leak into the rendered output.

This test pins the contract: when the rendered HTML is scanned for the
metadata-payload tokens (``first_seen_utc``, ``evidence_count`` etc.),
none of them appear, while the body of the pattern block remains
visible.
"""

from __future__ import annotations

import re

import pytest


def _strip_html_comments(text: str) -> str:
    """Approximate the CommonMark/Obsidian HTML-comment stripping rule.

    CommonMark renders ``<!-- ... -->`` as an HTML block whose contents
    are emitted verbatim into the HTML output, but every standards-
    compliant browser then *suppresses* the comment when displaying the
    page. From the user's perspective the text is invisible.

    For a unit test we therefore mimic the browser side: drop the
    contents of every comment before checking what the reader sees.
    """
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def test_metadata_fence_is_invisible_in_rendered_markdown() -> None:
    from agentic_swmm.memory.lessons_metadata import write_metadata

    block = (
        "## peak_flow_parse_missing\n"
        "\n"
        "Observed in 2 run(s): `runner-check`, `runner-fixed`.\n"
        "\n"
        "The peak flow value could not be located in the parsed RPT output.\n"
    )
    meta = {
        "first_seen_utc": "2026-03-01T10:23:00Z",
        "last_seen_utc": "2026-05-12T14:08:00Z",
        "evidence_count": 7,
        "evidence_runs": ["tecnopolo-199401-prepared"],
        "status": "active",
        "confidence_score": 4.92,
        "half_life_days": 90,
    }
    rendered = _strip_html_comments(write_metadata(block, meta))

    # None of the metadata identifiers should remain visible.
    for needle in (
        "first_seen_utc",
        "last_seen_utc",
        "evidence_count",
        "evidence_runs",
        "confidence_score",
        "half_life_days",
    ):
        assert needle not in rendered, (
            f"metadata token {needle!r} leaked into rendered output: "
            f"{rendered!r}"
        )

    # Body content stays visible to the reader.
    assert "The peak flow value could not be located" in rendered
    assert "## peak_flow_parse_missing" in rendered


def test_metadata_fence_uses_single_html_comment_block() -> None:
    """The fence is one contiguous HTML comment around the YAML payload."""
    from agentic_swmm.memory.lessons_metadata import write_metadata

    block = "## peak_flow_parse_missing\n\nBody.\n"
    meta = {
        "first_seen_utc": "2026-05-14T00:00:00Z",
        "last_seen_utc": "2026-05-14T00:00:00Z",
        "evidence_count": 1,
        "evidence_runs": [],
        "status": "active",
        "confidence_score": 1.0,
        "half_life_days": 90,
    }
    updated = write_metadata(block, meta)

    # The fence sentinels appear exactly once each, in order.
    assert updated.count("<!-- aiswmm-metadata") == 1
    assert updated.count("/aiswmm-metadata -->") == 1
    open_idx = updated.index("<!-- aiswmm-metadata")
    close_idx = updated.index("/aiswmm-metadata -->")
    assert open_idx < close_idx

    # The fence must be a single HTML comment: between the opening
    # ``<!--`` and the closing ``-->`` there must be no extra
    # ``-->`` (which would close the comment early and expose the
    # payload to the rendered output).
    fenced_region = updated[open_idx : close_idx + len("/aiswmm-metadata -->")]
    # The fenced region starts with ``<!--`` and ends with ``-->`` and
    # contains no intermediate ``-->``.
    assert fenced_region.startswith("<!--")
    assert fenced_region.endswith("-->")
    interior = fenced_region[len("<!--") : -len("-->")]
    assert "-->" not in interior
